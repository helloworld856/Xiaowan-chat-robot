import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from threading import BoundedSemaphore
from time import monotonic

from fastapi import APIRouter, Depends, HTTPException, Request

import agent_config
from agent_config.agent_factory import create_model
from log_config import logger
from personal_model_config import PersonalModel
from app.api.credential_route import CredentialRoute
from settings_config import settings_store

from pydantic import BaseModel
from settings_config import ModelSettings


class ModelConfigRequest(ModelSettings):
    pass


class ModelConfigResponse(BaseModel):
    status: bool  # 是否验证成功
    info: str     # 提示信息


router = APIRouter(route_class=CredentialRoute)
MODEL_VALIDATION_CAPACITY = 2
validation_pool = ThreadPoolExecutor(max_workers=MODEL_VALIDATION_CAPACITY, thread_name_prefix='model-test')
validation_slots = BoundedSemaphore(MODEL_VALIDATION_CAPACITY)
VALIDATION_TIMEOUT_SECONDS = 20
MODEL_SWITCH_TIMEOUT_SECONDS = 25
MODEL_API_KEYS = {'deepseek': 'DEEPSEEK_API_KEY', 'tongyi': 'TONGYI_API_KEY'}


class ModelValidationBusy(Exception):
    pass


def use_agents(model, agents):
    with agent_config.agent_lock:
        agent_config.analysis_agent, agent_config.text_agent, agent_config.friend_agent = agents
        agent_config.active_model = model


async def require_admin(request: Request):
    if request.state.user.role != 'admin':
        raise HTTPException(403, '只有管理员可以配置模型')


async def require_personal_account(request: Request):
    if request.state.user.role != 'user':
        raise HTTPException(403, '管理员的 API Key 由服务端配置')


def activate_saved_model(model):
    if model is None:
        return
    api_key = os.getenv(MODEL_API_KEYS[model.model_merchant])
    if not api_key:
        logger.warning('未配置已保存模型所需的服务端 API Key')
        return
    try:
        agents = agent_config.create_agents(model.model_merchant, api_key, model.model_name, model.base_url)
        use_agents(model, agents)
    except Exception:
        logger.warning('启动时载入已保存的模型失败，请管理员检查配置')


def validate_connection(llm):
    # 前端 30 秒超时前返回；后台验证即使稍后结束也不会继续切换模型。
    if not validation_slots.acquire(blocking=False):
        raise ModelValidationBusy

    def invoke():
        logger.info('模型连接测试开始')
        llm.invoke('hi')
        logger.info('模型连接测试完成')

    try:
        future = validation_pool.submit(copy_context().run, invoke)
    except Exception:
        validation_slots.release()
        raise
    future.add_done_callback(lambda _: validation_slots.release())
    try:
        future.result(timeout=VALIDATION_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        raise TimeoutError('模型连接测试超时') from None


@router.post("/model", response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def validate_and_switch_model(request: ModelConfigRequest):
    return configure_model(request, activate=True)


@router.post('/model/test', response_model=ModelConfigResponse, dependencies=[Depends(require_admin)])
def test_model(request: ModelConfigRequest):
    return configure_model(request, activate=False)


@router.post('/model/personal', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def save_personal_model(model: PersonalModel, request: Request):
    return configure_personal_model(model, request, activate=True)


@router.post('/model/personal/test', response_model=ModelConfigResponse, dependencies=[Depends(require_personal_account)])
def test_personal_model(model: PersonalModel, request: Request):
    return configure_personal_model(model, request, activate=False)


def configure_personal_model(model: PersonalModel, request: Request, activate: bool):
    try:
        llm = create_model(model.model_merchant, model.api_key.get_secret_value(), model.model_name,
                           0.1, max_tokens=10, validation_timeout=8, official_only=True)
        validate_connection(llm)
        if not activate:
            return ModelConfigResponse(status=True, info='连接测试通过')
        agents = model.create_agents()
        try:
            request.app.state.personal_models.update(request.state.user.id, model, agents)
        except (OSError, ValueError) as error:
            logger.warning(f'个人模型保存失败: {type(error).__name__}')
            return ModelConfigResponse(status=False, info='个人模型保存失败，请稍后重试')
        return ModelConfigResponse(status=True, info='个人模型已保存')
    except Exception as error:
        return connection_failure(error)


def configure_model(request: ModelConfigRequest, activate: bool):
    """验证模型配置并切换代理"""
    deadline = monotonic() + MODEL_SWITCH_TIMEOUT_SECONDS
    merchant = request.model_merchant.lower().strip()
    model_name = request.model_name.strip()

    if not model_name:
        return ModelConfigResponse(status=False, info="模型名称不能为空")

    try:
        api_key_name = MODEL_API_KEYS.get(merchant)
        if api_key_name is None:
            return ModelConfigResponse(status=False, info="模型配置无效！")

        api_key = os.getenv(api_key_name)
        if not api_key:
            return ModelConfigResponse(status=False, info='尚未配置此厂商的服务端 API Key')
        llm = create_model(merchant, api_key, model_name, 0.1, max_tokens=10,
                           base_url=request.base_url, validation_timeout=8)
        validate_connection(llm)

        if not activate:
            return ModelConfigResponse(status=True, info='连接测试通过，模型配置尚未修改')

        # 三个代理和文件都准备成功后，才替换当前可用代理。
        agents = agent_config.create_agents(merchant, api_key, model_name, request.base_url)
        remaining = deadline - monotonic()
        if remaining <= 0 or not agent_config.agent_lock.acquire(timeout=remaining):
            return ModelConfigResponse(status=False, info='当前正在回复，请稍后重试切换模型')
        try:
            try:
                settings_store.update_model(request)
            except (OSError, ValueError) as error:
                logger.warning(f'管理员模型保存失败: {type(error).__name__}')
                return ModelConfigResponse(status=False, info='配置保存失败，原有模型仍在使用')
            use_agents(request, agents)
        finally:
            agent_config.agent_lock.release()

        logger.info(f"模型配置切换成功: {merchant}/{model_name}")
        return ModelConfigResponse(status=True, info=f"已切换到 {merchant}/{model_name}")

    except Exception as error:
        return connection_failure(error)


def connection_failure(error: Exception):
    logger.warning(f'模型配置失败: {type(error).__name__}')
    if isinstance(error, ModelValidationBusy):
        return ModelConfigResponse(status=False, info='模型连接测试繁忙，请稍后重试')
    if isinstance(error, TimeoutError):
        return ModelConfigResponse(status=False, info='请求超时，请检查网络')
    normalized_error = str(error).lower()
    if any(marker in normalized_error for marker in ('401', 'unauthorized', 'invalid_api_key', 'invalid api key')):
        return ModelConfigResponse(status=False, info='API Key 无效或已过期')
    if '404' in normalized_error or 'not found' in normalized_error:
        return ModelConfigResponse(status=False, info='模型不存在')
    if 'timeout' in normalized_error or 'timed out' in normalized_error:
        return ModelConfigResponse(status=False, info='请求超时，请检查网络')
    return ModelConfigResponse(status=False, info='连接失败，请检查模型名、API Key 和接口配置')
