from fastapi import APIRouter, Depends, HTTPException, Request

from app.llm.admin_settings import ApiKey, ModelSettings
from app.llm.configuration import ModelConfigResponse
from app.llm.personal_models import PersonalModel

from .credential_route import CredentialRoute


class ModelConfigRequest(ModelSettings):
    api_key: ApiKey | None = None

    def selection(self) -> ModelSettings:
        return ModelSettings.model_validate(self.model_dump(
            include={'model_merchant', 'model_name', 'base_url'}, exclude_unset=True,
        ))


router = APIRouter(route_class=CredentialRoute)


async def require_admin(request: Request):
    if request.state.user.role != 'admin':
        raise HTTPException(403, '只有管理员可以配置模型')


async def require_personal_account(request: Request):
    if request.state.user.role != 'user':
        raise HTTPException(403, '管理员的 API Key 由服务端配置')


@router.post('/model', response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def validate_and_switch_model(model: ModelConfigRequest, request: Request):
    return request.app.state.model_configuration.configure_admin(
        model.selection(), activate=True, new_key=model.api_key)


@router.post('/model/test', response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def test_model(model: ModelConfigRequest, request: Request):
    return request.app.state.model_configuration.configure_admin(
        model.selection(), activate=False, new_key=model.api_key)


# 个人模型的保存目标由会话账号决定，密钥只用于这次测试或保存。
@router.post('/model/personal', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def save_personal_model(model: PersonalModel, request: Request):
    return request.app.state.model_configuration.configure_personal(request.state.user.id, model, activate=True)


@router.post('/model/personal/test', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def test_personal_model(model: PersonalModel, request: Request):
    return request.app.state.model_configuration.configure_personal(request.state.user.id, model, activate=False)
