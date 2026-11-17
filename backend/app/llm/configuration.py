"""Validate and activate model configurations for the administrator and individual accounts."""

import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from threading import BoundedSemaphore, RLock
from time import monotonic

from langchain_core.runnables import Runnable
from pydantic import BaseModel

from app.account_logging import logger
from app.llm import factory
from app.llm.admin_settings import ModelSettings, SettingsStore
from app.llm.personal_models import PersonalModel, PersonalModelStore


MODEL_VALIDATION_CAPACITY = 2
VALIDATION_TIMEOUT_SECONDS = 20
MODEL_SWITCH_TIMEOUT_SECONDS = 25
MODEL_API_KEYS = {'deepseek': 'DEEPSEEK_API_KEY', 'tongyi': 'TONGYI_API_KEY'}

_validation_pool = ThreadPoolExecutor(max_workers=MODEL_VALIDATION_CAPACITY, thread_name_prefix='model-test')
_validation_slots = BoundedSemaphore(MODEL_VALIDATION_CAPACITY)
AgentSet = tuple[Runnable, Runnable, Runnable]


class ModelConfigResponse(BaseModel):
    status: bool
    info: str


class ModelValidationBusy(Exception):
    pass


class AdminModelRuntime:
    def __init__(self):
        self.lock = RLock()
        self._active: tuple[ModelSettings, AgentSet] | None = None

    def snapshot(self) -> tuple[ModelSettings, AgentSet] | None:
        with self.lock:
            return self._active

    def publish(self, model: ModelSettings, agents: AgentSet) -> None:
        with self.lock:
            self._active = (model, agents)


def _validate_connection(client) -> None:
    if not _validation_slots.acquire(blocking=False):
        raise ModelValidationBusy

    def invoke():
        logger.info('模型连接测试开始')
        client.invoke('hi')
        logger.info('模型连接测试完成')

    try:
        future = _validation_pool.submit(copy_context().run, invoke)
    except Exception:
        _validation_slots.release()
        raise
    future.add_done_callback(lambda _: _validation_slots.release())
    try:
        future.result(timeout=VALIDATION_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        raise TimeoutError('模型连接测试超时') from None


def _verify_model(merchant: str, key: str, name: str, base_url: str, *, official_only: bool) -> None:
    client = factory.create_model(
        merchant, key, name, 0.1, max_tokens=10, base_url=base_url,
        validation_timeout=8, official_only=official_only,
    )
    _validate_connection(client)


def _connection_failure(error: Exception) -> ModelConfigResponse:
    logger.warning('模型配置失败: %s', type(error).__name__)
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


class ModelConfigurationService:
    def __init__(self, settings: SettingsStore, personal_models: PersonalModelStore, admin_runtime: AdminModelRuntime):
        self.settings = settings
        self.personal_models = personal_models
        self.admin_runtime = admin_runtime

    def activate_saved_model(self, model: ModelSettings | None) -> None:
        if model is None:
            return
        key = os.getenv(MODEL_API_KEYS[model.model_merchant])
        if not key:
            logger.warning('未配置已保存模型所需的服务端 API Key')
            return
        try:
            agents = factory.create_agents(model.model_merchant, key, model.model_name, model.base_url)
            self.admin_runtime.publish(model, agents)
        except Exception:
            logger.warning('启动时载入已保存的模型失败，请管理员检查配置')

    def admin_status(self) -> tuple[ModelSettings | None, bool]:
        with self.admin_runtime.lock:
            active = self.admin_runtime.snapshot()
            if active is not None:
                return active[0], True
            try:
                return self.settings.get().model, False
            except (OSError, ValueError):
                return None, False

    def configure_admin(self, model: ModelSettings, *, activate: bool) -> ModelConfigResponse:
        deadline = monotonic() + MODEL_SWITCH_TIMEOUT_SECONDS
        merchant = model.model_merchant.lower().strip()
        name = model.model_name.strip()
        if not name:
            return ModelConfigResponse(status=False, info='模型名称不能为空')
        try:
            key_name = MODEL_API_KEYS.get(merchant)
            if key_name is None:
                return ModelConfigResponse(status=False, info='模型配置无效！')
            key = os.getenv(key_name)
            if not key:
                return ModelConfigResponse(status=False, info='尚未配置此厂商的服务端 API Key')
            agents = self._prepare_model(merchant, key, name, model.base_url, official_only=False, activate=activate)
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过，模型配置尚未修改')

            remaining = deadline - monotonic()
            if remaining <= 0 or not self.admin_runtime.lock.acquire(timeout=remaining):
                return ModelConfigResponse(status=False, info='当前正在回复，请稍后重试切换模型')
            try:
                try:
                    self.settings.update_model(model)
                except (OSError, ValueError) as error:
                    logger.warning('管理员模型保存失败: %s', type(error).__name__)
                    return ModelConfigResponse(status=False, info='配置保存失败，原有模型仍在使用')
                self.admin_runtime.publish(model, agents)
            finally:
                self.admin_runtime.lock.release()

            logger.info('模型配置切换成功: %s/%s', merchant, name)
            return ModelConfigResponse(status=True, info=f'已切换到 {merchant}/{name}')
        except Exception as error:
            return _connection_failure(error)

    def configure_personal(self, user_id: int, model: PersonalModel, *, activate: bool) -> ModelConfigResponse:
        try:
            agents = self._prepare_model(
                model.model_merchant, model.api_key.get_secret_value(), model.model_name, '',
                official_only=True, activate=activate,
            )
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过')
            try:
                self.personal_models.update(user_id, model, agents)
            except (OSError, ValueError) as error:
                logger.warning('个人模型保存失败: %s', type(error).__name__)
                return ModelConfigResponse(status=False, info='个人模型保存失败，请稍后重试')
            return ModelConfigResponse(status=True, info='个人模型已保存')
        except Exception as error:
            return _connection_failure(error)

    @staticmethod
    def _prepare_model(merchant: str, key: str, name: str, base_url: str, *,
                       official_only: bool, activate: bool) -> AgentSet | None:
        _verify_model(merchant, key, name, base_url, official_only=official_only)
        if not activate:
            return None
        return factory.create_agents(merchant, key, name, base_url, official_only=official_only)
