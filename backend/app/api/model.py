from fastapi import APIRouter, Depends, HTTPException, Request

from app.account_logging import log_event
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
        log_event('model_permission_denied', system=True, user_id=request.state.user.id, reason='admin_required')
        raise HTTPException(403, '只有管理员可以配置模型')


async def require_personal_account(request: Request):
    if request.state.user.role != 'user':
        log_event('model_permission_denied', system=True, user_id=request.state.user.id, reason='personal_account_required')
        raise HTTPException(403, '管理员的 API Key 由服务端配置')


def _log_result(action: str, result: ModelConfigResponse) -> ModelConfigResponse:
    log_event('model_operation', action=action, outcome='succeeded' if result.status else 'failed')
    return result


@router.post('/model', response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def validate_and_switch_model(model: ModelConfigRequest, request: Request):
    result = request.app.state.model_configuration.configure_admin(
        model.selection(), activate=True, new_key=model.api_key)
    log_event('global_model_switch', system=True, user_id=request.state.user.id,
              outcome='succeeded' if result.status else 'failed', provider=model.model_merchant,
              model=model.model_name if result.status else None)
    return _log_result('global_switch', result)


@router.post('/model/test', response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def test_model(model: ModelConfigRequest, request: Request):
    result = request.app.state.model_configuration.configure_admin(
        model.selection(), activate=False, new_key=model.api_key)
    return _log_result('global_test', result)


# 个人模型的保存目标由会话账号决定，密钥只用于这次测试或保存。
@router.post('/model/personal', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def save_personal_model(model: PersonalModel, request: Request):
    result = request.app.state.model_configuration.configure_personal(request.state.user.id, model, activate=True)
    return _log_result('personal_save', result)


@router.post('/model/personal/test', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def test_personal_model(model: PersonalModel, request: Request):
    result = request.app.state.model_configuration.configure_personal(request.state.user.id, model, activate=False)
    return _log_result('personal_test', result)
