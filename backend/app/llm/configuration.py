"""Validate and activate model configurations for the administrator and individual accounts."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from threading import BoundedSemaphore, RLock
from time import monotonic

from langchain_core.runnables import Runnable
from pydantic import BaseModel, SecretStr

from app.account_logging import logger
from app.llm import factory
from app.llm.admin_models import AdminModelFile, AdminModelStore
from app.llm.admin_settings import ModelSettings
from app.llm.model_catalog import ModelEndpoint
from app.llm.personal_models import PersonalModel, PersonalModelStore
from app.llm.public_endpoint import PublicEndpointError


MODEL_VALIDATION_CAPACITY = 2
VALIDATION_TIMEOUT_SECONDS = 20
MODEL_SWITCH_TIMEOUT_SECONDS = 25

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

    # 整组配置与代理一起读取，聊天开始后持有这一轮使用的代理。
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
    # 请求超时也要等探测线程退出，再归还验证名额。
    future.add_done_callback(lambda _: _validation_slots.release())
    try:
        future.result(timeout=VALIDATION_TIMEOUT_SECONDS)
    except TimeoutError:
        future.cancel()
        raise TimeoutError('模型连接测试超时') from None


def _verify_model(merchant: str, key: str, name: str, endpoint: ModelEndpoint, *, public_only: bool) -> None:
    client = factory.create_model(
        merchant, key, name, 0.1, max_tokens=10, base_url=endpoint.base_url,
        validation_timeout=8, adapter=endpoint.adapter, public_only=public_only,
    )
    _validate_connection(client)


def _connection_failure(error: Exception) -> ModelConfigResponse:
    logger.warning('模型配置失败: %s', type(error).__name__)
    for cause in (error, error.__cause__):
        if isinstance(cause, PublicEndpointError):
            return ModelConfigResponse(status=False, info=str(cause))
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
    def __init__(self, admin_models: AdminModelStore, personal_models: PersonalModelStore, admin_runtime: AdminModelRuntime):
        self.admin_models = admin_models
        self.personal_models = personal_models
        self.admin_runtime = admin_runtime

    def _admin_endpoint(self, model: ModelSettings) -> ModelEndpoint:
        provider = self.personal_models.catalog.providers.get(model.model_merchant)
        if model.base_url and (provider is None or model.base_url != provider.base_url):
            return ModelEndpoint(base_url=model.base_url, adapter='openai')
        return self.personal_models.catalog.endpoint(model.model_merchant)

    def activate_saved_model(self, legacy: Callable[[], AdminModelFile]) -> None:
        try:
            saved = self.admin_models.initialize(legacy)
            model = saved.model
            if model is None:
                return
            key = saved.key_for(model.model_merchant)
            if key is None:
                logger.warning('未配置已保存模型所需的管理员 API Key')
                return
            endpoint = self._admin_endpoint(model)
            agents = factory.create_agents(model.model_merchant, key.get_secret_value(), model.model_name,
                                           base_url=endpoint.base_url, adapter=endpoint.adapter)
            # 启动时从已保存配置装配代理，连接探测留给显式测试或保存。
            self.admin_runtime.publish(model, agents)
        except Exception:
            logger.warning('启动时载入已保存的模型失败，请管理员检查配置')

    def admin_status(self) -> tuple[ModelSettings | None, bool]:
        with self.admin_runtime.lock:
            active = self.admin_runtime.snapshot()
            if active is not None:
                return active[0], True
            try:
                return self.admin_models.get().model, False
            except (OSError, ValueError):
                return None, False

    def configure_admin(self, model: ModelSettings, *, activate: bool,
                        new_key: SecretStr | None = None) -> ModelConfigResponse:
        deadline = monotonic() + MODEL_SWITCH_TIMEOUT_SECONDS
        merchant = model.model_merchant
        name = model.model_name
        if not self.personal_models.catalog.allows(merchant, name):
            return ModelConfigResponse(status=False, info='请选择全局配置中该厂商的模型')
        use_saved_address = 'base_url' not in model.model_fields_set
        try:
            saved = self.admin_models.get() if use_saved_address or new_key is None else None
            if use_saved_address and saved is not None:
                entry = saved.providers.get(merchant)
                if entry is not None:
                    model = model.model_copy(update={'base_url': entry.base_url})
            key = new_key if new_key is not None else saved.key_for(merchant)
            if key is None:
                return ModelConfigResponse(status=False, info='请先填写此厂商的 API Key')
            agents = self._prepare_model(merchant, key.get_secret_value(), name, self._admin_endpoint(model),
                                         activate=activate)
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过，模型配置尚未修改')

            remaining = deadline - monotonic()
            if remaining <= 0 or not self.admin_runtime.lock.acquire(timeout=remaining):
                return ModelConfigResponse(status=False, info='当前正在回复，请稍后重试切换模型')
            try:
                try:
                    # 先保存文件，再发布新代理，失败时旧模型仍可继续处理聊天。
                    saved_ok = self.admin_models.update_model(
                        model, key, replace_key=new_key is not None, keep_address=use_saved_address,
                    )
                except (OSError, ValueError) as error:
                    logger.warning('管理员模型保存失败: %s', type(error).__name__)
                    return ModelConfigResponse(status=False, info='配置保存失败，原有模型仍在使用')
                if not saved_ok:
                    return ModelConfigResponse(status=False, info='此模型的配置已更新，请重新测试或保存')
                self.admin_runtime.publish(model, agents)
            finally:
                self.admin_runtime.lock.release()

            logger.info('模型配置切换成功: %s/%s', merchant, name)
            return ModelConfigResponse(status=True, info=f'已切换到 {merchant}/{name}')
        except Exception as error:
            return _connection_failure(error)

    def configure_personal(self, user_id: int, model: PersonalModel, *, activate: bool) -> ModelConfigResponse:
        if not self.personal_models.catalog.allows(model.model_merchant, model.model_name):
            return ModelConfigResponse(status=False, info='请选择全局配置中该厂商的模型')
        try:
            selected = self.personal_models.selection(user_id, model)
            if selected is None:
                return ModelConfigResponse(status=False, info='请先填写此厂商的 API Key')
            agents = self._prepare_model(
                selected.model_merchant, selected.api_key.get_secret_value(), selected.model_name,
                ModelEndpoint(base_url=selected.base_url, adapter=selected.adapter), activate=activate,
                public_only=selected.public_only,
            )
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过')
            try:
                if not self.personal_models.update(user_id, model, agents, checked=selected):
                    return ModelConfigResponse(status=False, info='此模型的配置已更新，请重新测试或保存')
            except (OSError, ValueError) as error:
                logger.warning('个人模型保存失败: %s', type(error).__name__)
                return ModelConfigResponse(status=False, info='个人模型保存失败，请稍后重试')
            return ModelConfigResponse(status=True, info='个人模型已保存')
        except Exception as error:
            return _connection_failure(error)

    @staticmethod
    def _prepare_model(merchant: str, key: str, name: str, endpoint: ModelEndpoint, *,
                       activate: bool, public_only: bool = False) -> AgentSet | None:
        _verify_model(merchant, key, name, endpoint, public_only=public_only)
        if not activate:
            return None
        return factory.create_agents(merchant, key, name, base_url=endpoint.base_url, adapter=endpoint.adapter,
                                     public_only=public_only)
