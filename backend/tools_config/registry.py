import inspect
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from contextvars import copy_context
from functools import wraps
from logging import WARNING

from langchain_core.tools import StructuredTool
from pydantic import ValidationError, validate_call

from app.account_logging import log_event
from app.chat.progress import ChatCancelled, check_cancelled
from app.chat.tool_context import ToolRiskLevel, current_tool_context

from .history_tool.history import search_chat_history
from .history_tool.history_spec import SEARCH_HISTORY_SPEC
from .search_tool.searxng import web_search
from .search_tool.search_spec import WEB_SEARCH_SPEC
from .time_tool.time import get_current_time
from .time_tool.time_spec import CURRENT_TIME_SPEC
from .url_tool.url_reader import read_url
from .url_tool.url_spec import READ_URL_SPEC
from .weather_tool.weather import get_weather
from .weather_tool.weather_spec import WEATHER_SPEC

DEFAULT_TOOL_TIMEOUT_SECONDS = 8.0
_TOOL_TIMEOUT_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix='xiaowan-tool')


def _summarize_action(name: str, kwargs: dict) -> str:
    shown = ', '.join(f'{k}={v!r}' for k, v in kwargs.items() if k != 'confirm_token')
    return f'{name}({shown})' if shown else name


def register_tool(
    spec: dict,
    handler: Callable,
    *,
    risk_level: ToolRiskLevel = 'read',
    taints_context: bool = False,
    timeout_seconds: float | None = DEFAULT_TOOL_TIMEOUT_SECONDS,
    describe_action: Callable[[dict], str] | None = None,
) -> StructuredTool:
    function = spec["function"]
    tool_name = function["name"]
    sig = inspect.signature(handler)

    def _guarded(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        arg_map = dict(bound.arguments)

        ctx = current_tool_context()
        if ctx is not None:
            if ctx.tool_calls >= ctx.max_tool_calls:
                log_event('tool_blocked', level=WARNING, tool=tool_name, reason='turn_call_limit')
                return '[工具提示：本轮工具查询次数已达上限，请直接根据已有信息自然回复对方]'

            if risk_level != 'read' and ctx.external_tainted:
                log_event('tool_blocked', level=WARNING, tool=tool_name, reason='external_content_tainted')
                return '[安全拦截：本轮读取过外部网页内容，为防误触已暂停修改类操作；请先在对话中向对方口头确认，等下一轮再执行]'

            if risk_level == 'write_safe' and ctx.safe_writes >= ctx.max_safe_writes:
                log_event('tool_blocked', level=WARNING, tool=tool_name, reason='safe_write_limit')
                return '[工具提示：本轮新增记录已达上限，请先自然回复对方]'

            if risk_level == 'write_destructive':
                token = str(arg_map.get('confirm_token') or '').strip()
                confirmed = ctx.confirmations.consume(ctx.user_id, tool_name, token, arguments=arg_map)
                if confirmed is None:
                    summary = describe_action(arg_map) if describe_action is not None else _summarize_action(tool_name, arg_map)
                    pending = ctx.confirmations.stage(ctx.user_id, tool_name, summary, arg_map)
                    log_event('tool_confirmation_required', tool=tool_name)
                    return (
                        f'[待确认操作：将执行「{summary}」，确认码 confirm_token="{pending.token}"（5 分钟内有效）。'
                        '本次尚未真正执行，请先用自然朋友口吻向对方确认是否真的要这样做，等对方下一轮明确回复同意后再传入该 confirm_token 执行]'
                    )

            ctx.tool_calls += 1
            if risk_level == 'write_safe':
                ctx.safe_writes += 1
            if taints_context:
                ctx.external_tainted = True

        if timeout_seconds is not None and timeout_seconds > 0:
            future = _TOOL_TIMEOUT_EXECUTOR.submit(copy_context().run, handler, *args, **kwargs)
            return future.result(timeout=timeout_seconds)
        return handler(*args, **kwargs)

    _guarded.__signature__ = sig
    _guarded.__annotations__ = dict(getattr(handler, '__annotations__', {}))
    _guarded.__name__ = handler.__name__
    _guarded.__module__ = handler.__module__
    validate = validate_call(_guarded)

    defaulted_non_none = frozenset(
        name
        for name, param in sig.parameters.items()
        if param.default is not inspect.Parameter.empty and param.default is not None
    )

    @wraps(handler)
    def invoke(*args, **kwargs):
        check_cancelled()
        # 部分厂商模型会对未使用的可选参数显式传 null，自动回退为函数声明的默认值。
        if kwargs and defaulted_non_none:
            kwargs = {k: v for k, v in kwargs.items() if not (v is None and k in defaulted_non_none)}
        try:
            result = validate(*args, **kwargs)
        except ChatCancelled:
            raise
        except ValidationError:
            log_event('tool_failure', level=WARNING, tool=tool_name, reason='argument_validation_failed')
            raise
        except FuturesTimeoutError:
            log_event('tool_failure', level=WARNING, tool=tool_name, reason='timeout')
            return '[工具提示：查询等待超时，暂时无法获取结果，请自然告知对方稍后再试]'
        except Exception as error:
            log_event('tool_failure', level=WARNING, tool=tool_name, error=type(error).__name__)
            return '[工具提示：服务暂时不可用，请自然接话而不要暴露内部错误]'
        check_cancelled()
        return result

    # JSON Schema 用于模型绑定，Python 函数签名用于校验实际参数。
    return StructuredTool.from_function(
        func=invoke,
        name=tool_name,
        description=function["description"],
        args_schema=function["parameters"],
        infer_schema=False,
    )


CHAT_TOOLS = [
    register_tool(CURRENT_TIME_SPEC, get_current_time, risk_level='read', timeout_seconds=None),
    register_tool(WEATHER_SPEC, get_weather, risk_level='read', timeout_seconds=8.0),
    register_tool(SEARCH_HISTORY_SPEC, search_chat_history, risk_level='read', timeout_seconds=5.0),
    register_tool(WEB_SEARCH_SPEC, web_search, risk_level='read', taints_context=True, timeout_seconds=12.0),
    register_tool(READ_URL_SPEC, read_url, risk_level='read', taints_context=True, timeout_seconds=7.0),
]
