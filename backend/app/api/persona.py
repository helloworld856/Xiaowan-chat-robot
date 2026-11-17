import urllib.parse
from fastapi import APIRouter, Request
from app.llm.persona import persona

router = APIRouter()


@router.get("/persona")
def get_persona(request: Request):
    base_url = str(request.base_url)

    # AI头像
    bot_avatar_url = urllib.parse.urljoin(base_url, '/'.join(["avatars", persona.BOT_AVATAR]))
    # 用户头像
    user = request.state.user
    profile = request.app.state.profile.get(user.id, user.username)
    # 角色资料共用，用户头像则从当前账号的资料中读取。
    user_avatar_url = urllib.parse.urljoin(base_url, profile.avatar_url)

    return {
        "BOT_AVATAR": bot_avatar_url,
        "BOT_NAME": persona.BOT_NAME,
        "BOT_BIRTHDAY": persona.BOT_BIRTHDAY,
        "BOT_BIRTHPLACE": persona.BOT_BIRTHPLACE,
        "USER_AVATAR": user_avatar_url,
    }
