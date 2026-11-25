from logging import WARNING

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.account_logging import log_event
from app.accounts.profile import AVATARS, Profile
from app.system_config import AvatarId

router = APIRouter(tags=['profile'])


# 资料文件可以用默认字段初始化，用户保存时必须明确提交两项。
class ProfileRequest(Profile):
    nickname: str = Field(min_length=1, max_length=256)
    avatar_id: AvatarId = Field(...)


def profile_response(username: str, profile: Profile, default_avatar_id: AvatarId):
    return {
        'username': username,
        **profile.model_dump(),
        'avatar_url': profile.avatar_url,
        'avatars': [{'id': avatar_id, 'name': name, 'url': f'/avatars/system/{avatar_id}.png'}
                    for avatar_id, name in AVATARS],
        'default_avatar_id': default_avatar_id,
    }


@router.get('/profile')
def get_profile(request: Request):
    user = request.state.user
    store = request.app.state.profile
    try:
        return profile_response(user.username, store.get(user.id, user.username), store.default_avatar_id)
    except (OSError, ValueError) as error:
        log_event('profile_read_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '读取个人资料失败，请检查 profile.json') from None


@router.post('/profile')
def save_profile(profile: ProfileRequest, request: Request):
    user = request.state.user
    store = request.app.state.profile
    limit = store.config.max_nickname_chars
    if len(profile.nickname) > limit:
        raise HTTPException(422, f'昵称最多为 {limit} 个字符')
    try:
        return profile_response(user.username, store.update(user.id, profile), store.default_avatar_id)
    except OSError as error:
        log_event('profile_save_failed', level=WARNING, error=type(error).__name__)
        raise HTTPException(500, '保存个人资料失败，原有资料仍保留') from None
