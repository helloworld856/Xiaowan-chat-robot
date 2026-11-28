"""Validate and activate model configurations for the administrator and individual accounts."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextvars import copy_context
from logging import WARNING
from threading import BoundedSemaphore, Event, RLock
from time import monotonic

from langchain_core.runnables import Runnable
from pydantic import BaseModel, SecretStr

from app.account_logging import discard_logs_after, log_event, logger
from app.llm import factory
from app.llm.admin_models import AdminModelFile, AdminModelStore
from app.llm.admin_settings import ModelSettings
from app.llm.model_catalog import ModelEndpoint
from app.llm.personal_models import PersonalModel, PersonalModelAddressChange, PersonalModelStore
from app.llm.public_endpoint import PublicEndpointError
from app.system_config import ModelRuntimeConfig


VALIDATION_TIMEOUT_SECONDS = 20
MODEL_SWITCH_TIMEOUT_SECONDS = 25

AgentSet = tuple[Runnable, Runnable]


class ModelConfigResponse(BaseModel):
    status: bool
    info: str


class ModelValidationBusy(Exception):
    pass


class AdminEndpointNotAllowed(ValueError):
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


def _configuration_failure(reason: str, message: str, error: Exception | None = None) -> ModelConfigResponse:
    log_event('model_configuration_failed', level=WARNING, reason=reason,
              error=type(error).__name__ if error is not None else None)
    return ModelConfigResponse(status=False, info=message)


def _connection_failure(error: Exception) -> ModelConfigResponse:
    for cause in (error, error.__cause__):
        if isinstance(cause, PublicEndpointError):
            return _configuration_failure('endpoint_rejected', str(cause), cause)
    if isinstance(error, ModelValidationBusy):
        return _configuration_failure('validation_busy', '模型连接测试繁忙，请稍后重试', error)
    if isinstance(error, TimeoutError):
        return _configuration_failure('connection_timeout', '请求超时，请检查网络', error)
    normalized_error = str(error).lower()
    if any(marker in normalized_error for marker in ('401', 'unauthorized', 'invalid_api_key', 'invalid api key')):
        return _configuration_failure('provider_authentication_failed', 'API Key 无效或已过期', error)
    if '404' in normalized_error or 'not found' in normalized_error:
        return _configuration_failure('model_not_found', '模型不存在', error)
    if 'timeout' in normalized_error or 'timed out' in normalized_error:
        return _configuration_failure('connection_timeout', '请求超时，请检查网络', error)
    return _configuration_failure('connection_failed', '连接失败，请检查模型名、API Key 和接口配置', error)


class ModelConfigurationService:
    def __init__(self, admin_models: AdminModelStore, personal_models: PersonalModelStore, admin_runtime: AdminModelRuntime,
                 *, config: ModelRuntimeConfig | None = None):
        self.admin_models = admin_models
        self.personal_models = personal_models
        self.admin_runtime = admin_runtime
        capacity = (config or ModelRuntimeConfig()).validation_concurrency
        self._validation_pool = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix='model-test')
        self._validation_slots = BoundedSemaphore(capacity)

    def close(self) -> None:
        self._validation_pool.shutdown(cancel_futures=True)

    def _validate_connection(self, client) -> None:
        if not self._validation_slots.acquire(blocking=False):
            raise ModelValidationBusy

        cancelled = Event()

        def invoke():
            with discard_logs_after(cancelled):
                logger.info('模型连接测试开始')
                client.invoke('hi')
                logger.info('模型连接测试完成')

        try:
            future = self._validation_pool.submit(copy_context().run, invoke)
        except Exception:
            self._validation_slots.release()
            raise
        # 请求超时也要等探测线程退出，再归还验证名额。
        future.add_done_callback(lambda _: self._validation_slots.release())
        try:
            future.result(timeout=VALIDATION_TIMEOUT_SECONDS)
        except TimeoutError:
            # 超时的线程可能继续执行，避免它在请求结束和账号注销后重新打开日志。
            cancelled.set()
            future.cancel()
            raise TimeoutError('模型连接测试超时') from None

    def _admin_endpoint(self, model: ModelSettings) -> ModelEndpoint:
        catalog = self.personal_models.catalog
        default = catalog.endpoint(model.model_merchant)
        if not model.base_url or model.base_url == default.base_url:
            return default
        if model.base_url not in catalog.providers[model.model_merchant].admin_allowed_base_urls:
            raise AdminEndpointNotAllowed('管理员接口地址未获批准，请检查 system_config.json')
        return ModelEndpoint(base_url=model.base_url, adapter='openai')

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
            log_event('startup_model_loaded', system=True, provider=model.model_merchant, model=model.model_name)
        except AdminEndpointNotAllowed:
            logger.warning('管理员接口地址未获批准，请检查全局配置和 .env')
        except Exception as error:
            log_event('startup_model_load_failed', level=WARNING, system=True, error=type(error).__name__)

    def admin_status(self) -> tuple[ModelSettings | None, bool]:
        with self.admin_runtime.lock:
            active = self.admin_runtime.snapshot()
            if active is not None:
                return active[0], True
            try:
                return self.admin_models.get().model, False
            except (OSError, ValueError) as error:
                log_event('admin_model_read_failed', level=WARNING, error=type(error).__name__)
                return None, False

    def configure_admin(self, model: ModelSettings, *, activate: bool,
                        new_key: SecretStr | None = None) -> ModelConfigResponse:
        deadline = monotonic() + MODEL_SWITCH_TIMEOUT_SECONDS
        merchant = model.model_merchant
        name = model.model_name
        if not self.personal_models.catalog.allows(merchant, name):
            return _configuration_failure('model_not_allowed', '请选择全局配置中该厂商的模型')
        use_saved_address = 'base_url' not in model.model_fields_set
        try:
            saved = self.admin_models.get() if use_saved_address or new_key is None else None
            entry = saved.providers.get(merchant) if saved is not None else None
            saved_address = entry.base_url if entry is not None else ''
            if use_saved_address:
                model = model.model_copy(update={'base_url': saved_address})
            endpoint = self._admin_endpoint(model)
            key = new_key if new_key is not None else saved.key_for(merchant)
            if key is None:
                return _configuration_failure('missing_key', '请先填写此厂商的 API Key')
            if new_key is None:
                # 复用密钥时只允许访问它原来对应的有效地址。
                stored_endpoint = saved_address or self.personal_models.catalog.endpoint(merchant).base_url
                if endpoint.base_url != stored_endpoint:
                    return _configuration_failure('key_required_for_new_address', '更换接口地址时，请重新填写此地址的 API Key')
                model = model.model_copy(update={'base_url': saved_address})
            agents = self._prepare_model(merchant, key.get_secret_value(), name, endpoint, activate=activate)
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过，模型配置尚未修改')

            remaining = deadline - monotonic()
            if remaining <= 0 or not self.admin_runtime.lock.acquire(timeout=remaining):
                return _configuration_failure('switch_busy', '当前正在回复，请稍后重试切换模型')
            try:
                try:
                    # 先保存文件，再发布新代理，失败时旧模型仍可继续处理聊天。
                    saved_ok = self.admin_models.update_model(
                        model, key, replace_key=new_key is not None,
                        keep_address=use_saved_address or new_key is None,
                    )
                except (OSError, ValueError) as error:
                    return _configuration_failure('admin_save_failed', '配置保存失败，原有模型仍在使用', error)
                if not saved_ok:
                    return _configuration_failure('configuration_changed', '此模型的配置已更新，请重新测试或保存')
                self.admin_runtime.publish(model, agents)
            finally:
                self.admin_runtime.lock.release()

            log_event('admin_model_activated', provider=merchant, model=name)
            return ModelConfigResponse(status=True, info=f'已切换到 {merchant}/{name}')
        except AdminEndpointNotAllowed as error:
            return _configuration_failure('endpoint_not_allowed', str(error), error)
        except Exception as error:
            return _connection_failure(error)

    def configure_personal(self, user_id: int, model: PersonalModel, *, activate: bool) -> ModelConfigResponse:
        if not self.personal_models.catalog.allows(model.model_merchant, model.model_name):
            return _configuration_failure('model_not_allowed', '请选择全局配置中该厂商的模型')
        try:
            selected = self.personal_models.selection(user_id, model)
            if selected is None:
                return _configuration_failure('missing_key', '请先填写此厂商的 API Key')
            agents = self._prepare_model(
                selected.model_merchant, selected.api_key.get_secret_value(), selected.model_name,
                ModelEndpoint(base_url=selected.base_url, adapter=selected.adapter), activate=activate,
                public_only=selected.public_only,
            )
            if agents is None:
                return ModelConfigResponse(status=True, info='连接测试通过')
            try:
                saved = self.personal_models.update(user_id, model, agents, checked=selected)
            except PersonalModelAddressChange:
                saved = False
            except (OSError, ValueError) as error:
                return _configuration_failure('personal_save_failed', '个人模型保存失败，请稍后重试', error)
            if not saved:
                return _configuration_failure('configuration_changed', '此模型的配置已更新，请重新测试或保存')
            return ModelConfigResponse(status=True, info='个人模型已保存')
        except PersonalModelAddressChange as error:
            return _configuration_failure('key_required_for_new_address', str(error), error)
        except Exception as error:
            return _connection_failure(error)

    def _prepare_model(self, merchant: str, key: str, name: str, endpoint: ModelEndpoint, *,
                       activate: bool, public_only: bool = False) -> AgentSet | None:
        client = factory.create_model(
            merchant, key, name, 0.1, max_tokens=10, base_url=endpoint.base_url,
            validation_timeout=8, adapter=endpoint.adapter, public_only=public_only,
        )
        self._validate_connection(client)
        if not activate:
            return None
        return factory.create_agents(merchant, key, name, base_url=endpoint.base_url, adapter=endpoint.adapter,
                                     public_only=public_only)
