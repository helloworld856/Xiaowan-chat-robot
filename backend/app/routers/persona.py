import os
from fastapi import APIRouter, Request
from persona_config import persona

router = APIRouter()


@router.get("/persona")
def get_persona(request: Request):
    base_url = str(request.base_url)
    avatar_url = os.path.join(base_url, "avatars", persona.BOT_AVATAR)

    return {
        "BOT_AVATAR": avatar_url,
        "BOT_NAME": persona.BOT_NAME,
        "BOT_BIRTHDAY": persona.BOT_BIRTHDAY,
        "BOT_BIRTHPLACE": persona.BOT_BIRTHPLACE,
        "USER_AVATAR": os.path.join(base_url, 'user_avatar.png'),
    }
