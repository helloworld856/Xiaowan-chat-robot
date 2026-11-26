"""账号独立的用户画像存储：包含用户手动填写区与小晚随聊天积累的动态印象区。"""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.account_logging import logger
from app.accounts.files import UserDataPaths, read_text, replace_json, require_directory, require_file

MAX_LEARNED_ITEMS = 30
MAX_LEARNED_ITEM_CHARS = 120


class ManualPortrait(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    call_me: str = Field(default='', max_length=32)
    city: str = Field(default='', max_length=48)
    birthday: str = Field(default='', max_length=32)
    occupation: str = Field(default='', max_length=64)
    chat_preference: str = Field(default='', max_length=200)


class UserPortrait(BaseModel):
    model_config = ConfigDict(extra='forbid')
    manual: ManualPortrait = Field(default_factory=ManualPortrait)
    learned: list[str] = Field(default_factory=list)
    auto_learn: bool = True

    @field_validator('learned', mode='before')
    @classmethod
    def normalize_learned(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError('印象列表格式无效')
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                raise ValueError('印象条目须为文本')
            text = ' '.join(item.split()).strip()
            if not text:
                continue
            if len(text) > MAX_LEARNED_ITEM_CHARS:
                text = text[:MAX_LEARNED_ITEM_CHARS]
            if text not in seen:
                seen.add(text)
                cleaned.append(text)
            if len(cleaned) >= MAX_LEARNED_ITEMS:
                break
        return cleaned


def format_portrait_for_prompt(portrait: UserPortrait | None) -> str:
    if portrait is None:
        return '暂无'
    manual = portrait.manual
    manual_lines = []
    for label, value in (
        ('希望你这样称呼对方', manual.call_me),
        ('常住城市', manual.city),
        ('生日/年龄段', manual.birthday),
        ('职业/身份', manual.occupation),
        ('相处偏好/忌讳', manual.chat_preference),
    ):
        if value:
            manual_lines.append(f'- {label}：{value}')

    sections = []
    if manual_lines:
        sections.append('对方主动告诉你的信息（以此为准）：\n' + '\n'.join(manual_lines))
    if portrait.learned:
        learned_lines = [f'- {item}' for item in portrait.learned]
        sections.append('你平时聊着聊着记住的印象：\n' + '\n'.join(learned_lines))
    return '\n\n'.join(sections) if sections else '暂无'


class UserPortraitStore:
    def __init__(self, paths: UserDataPaths):
        self.paths = paths

    def path_for(self, user_id: int) -> Path:
        return self.paths.file(user_id, 'portrait.json')

    @staticmethod
    def read_file(path: Path) -> UserPortrait:
        return UserPortrait.model_validate_json(read_text(path))

    def get(self, user_id: int) -> UserPortrait:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            try:
                return self.read_file(path)
            except FileNotFoundError:
                return UserPortrait()
            except ValueError:
                logger.warning('用户画像文件格式无效，暂时使用空画像')
                return UserPortrait()

    def update(self, user_id: int, portrait: UserPortrait) -> UserPortrait:
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            self.paths.ensure_directory(user_id)
            replace_json(path, portrait.model_dump())
        return portrait

    def update_learned(self, user_id: int, learned: list[str]) -> UserPortrait | None:
        """只更新自动记忆区，不覆盖用户手动填写区与开关设置。"""
        path = self.path_for(user_id)
        with self.paths.lock(user_id):
            current = self.get(user_id)
            if not current.auto_learn:
                return None
            updated = UserPortrait(manual=current.manual, learned=learned, auto_learn=True)
            if updated.learned == current.learned:
                return updated
            self.paths.ensure_directory(user_id)
            replace_json(path, updated.model_dump())
            return updated

    def clear_all_learned(self) -> int:
        """启动时清空所有账号 portrait.json 中的自动印象，保留手动资料与开关。"""
        try:
            require_directory(self.paths.root)
            entries = list(self.paths.root.iterdir())
        except FileNotFoundError:
            return 0

        cleared = 0
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            path = entry / 'portrait.json'
            try:
                require_directory(entry)
                require_file(path)
                current = self.read_file(path)
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as error:
                logger.warning('无法读取账号目录 %s 的用户画像（%s）', entry.name, type(error).__name__)
                continue
            if not current.learned:
                continue
            updated = UserPortrait(manual=current.manual, learned=[], auto_learn=current.auto_learn)
            replace_json(path, updated.model_dump())
            cleared += 1
        return cleared


def learn_portrait_from_turn(summary_agent, current: UserPortrait,
                             user_input: str, assistant_replies: list[str]) -> list[str] | None:
    """用低温度代理从刚完成的一轮对话中提炼稳定的用户印象，无新信息时返回 None。"""
    from app.llm.output_parser import model_text, parse_model_json

    manual_text = format_portrait_for_prompt(UserPortrait(manual=current.manual))
    existing = '\n'.join(f'- {item}' for item in current.learned) if current.learned else '暂无'
    reply_text = ' / '.join(assistant_replies)
    prompt = f"""你在帮小晚维护「对这位朋友的长期印象」列表。

【用户自己填写的固定资料（勿重复记录，发生冲突时以这里为准）】
{manual_text}

【目前已记录的印象】
{existing}

【刚才这一轮对话】
对方：{user_input}
小晚：{reply_text}

请判断「对方」在这一轮里有没有明确透露值得长期记住的稳定信息（例如：兴趣爱好、饮食口味、作息习惯、性格特点、重要的人或宠物、近期持续在忙的事）。
规则：
1. 一次性闲聊、寒暄、当天天气、临时情绪波动不要记。
2. 固定资料里已经写过的内容不要重复记；如果对方明确更正了之前的印象，更新或替换旧条目。
3. 每条用简短中文写成一句话（不超过 40 字），总条数不超过 {MAX_LEARNED_ITEMS} 条。
4. 如果没有值得新增或修改的内容，直接返回 {{"changed":false,"items":[]}}。
5. 如果有更新，返回更新后的完整列表：{{"changed":true,"items":["条目1","条目2"]}}。

只输出标准 JSON 对象，省略代码块和额外说明。"""
    result = summary_agent.invoke({'messages': [{'role': 'user', 'content': prompt}]})
    messages = result.get('messages', [])
    text = model_text(messages[-1].content) if messages else ''
    parsed = parse_model_json(text, ('changed', 'items'))
    if not isinstance(parsed, dict) or parsed.get('changed') is not True:
        return None
    items = parsed.get('items')
    if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
        return None
    return UserPortrait.normalize_learned(items)
