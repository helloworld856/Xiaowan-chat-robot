import urllib.parse
from fastapi import APIRouter, Request
from persona_config import persona

router = APIRouter()


@router.get("/persona")
def get_persona(request: Request):
    base_url = str(request.base_url)

    # AI头像
    bot_avatar_url = urllib.parse.urljoin(base_url, '/'.join(["avatars", persona.BOT_AVATAR]))
    # 用户头像
    user_avatar_url = urllib.parse.urljoin(base_url, '/'.join(["avatars", 'user_avatar.png']))

    return {
        "BOT_AVATAR": bot_avatar_url,
        "BOT_NAME": persona.BOT_NAME,
        "BOT_BIRTHDAY": persona.BOT_BIRTHDAY,
        "BOT_BIRTHPLACE": persona.BOT_BIRTHPLACE,
        "USER_AVATAR": user_avatar_url,
    }
