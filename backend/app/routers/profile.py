from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from profile_config import AVATARS, DEFAULT_AVATAR_ID, AvatarId, Profile

router = APIRouter(tags=['profile'])


class ProfileRequest(Profile):
    nickname: str = Field(min_length=1, max_length=24)
    avatar_id: AvatarId = Field(...)


def profile_response(profile: Profile):
    return {
        'username': 'admin',
        **profile.model_dump(),
        'avatar_url': profile.avatar_url,
        'avatars': [{'id': avatar_id, 'name': name, 'url': f'/avatars/system/{avatar_id}.png'}
                    for avatar_id, name in AVATARS],
        'default_avatar_id': DEFAULT_AVATAR_ID,
    }


@router.get('/profile')
async def get_profile(request: Request):
    try:
        return profile_response(request.app.state.profile.get())
    except (OSError, ValueError):
        raise HTTPException(500, '读取个人资料失败，请检查 profile.json') from None


@router.post('/profile')
async def save_profile(profile: ProfileRequest, request: Request):
    try:
        return profile_response(request.app.state.profile.update(profile))
    except OSError:
        raise HTTPException(500, '保存个人资料失败，原有资料仍保留') from None
