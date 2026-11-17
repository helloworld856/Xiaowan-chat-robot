from fastapi import APIRouter, HTTPException, Request
from pydantic import Field

from app.accounts.profile import AVATARS, DEFAULT_AVATAR_ID, MAX_NICKNAME_LENGTH, AvatarId, Profile

router = APIRouter(tags=['profile'])


# 资料文件可以用默认字段初始化，用户保存时必须明确提交两项。
class ProfileRequest(Profile):
    nickname: str = Field(min_length=1, max_length=MAX_NICKNAME_LENGTH)
    avatar_id: AvatarId = Field(...)


def profile_response(username: str, profile: Profile):
    return {
        'username': username,
        **profile.model_dump(),
        'avatar_url': profile.avatar_url,
        'avatars': [{'id': avatar_id, 'name': name, 'url': f'/avatars/system/{avatar_id}.png'}
                    for avatar_id, name in AVATARS],
        'default_avatar_id': DEFAULT_AVATAR_ID,
    }


@router.get('/profile')
def get_profile(request: Request):
    user = request.state.user
    try:
        return profile_response(user.username, request.app.state.profile.get(user.id, user.username))
    except (OSError, ValueError):
        raise HTTPException(500, '读取个人资料失败，请检查 profile.json') from None


@router.post('/profile')
def save_profile(profile: ProfileRequest, request: Request):
    user = request.state.user
    try:
        return profile_response(user.username, request.app.state.profile.update(user.id, profile))
    except OSError:
        raise HTTPException(500, '保存个人资料失败，原有资料仍保留') from None
