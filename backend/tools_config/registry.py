from functools import wraps

from langchain_core.tools import StructuredTool
from pydantic import ValidationError, validate_call

from app.account_logging import log_event
from app.chat.progress import STAGE_LABELS, check_cancelled, report_stage

from .time_tool.time import get_current_time
from .time_tool.time_spec import CURRENT_TIME_SPEC

from .weather_tool.weather import get_weather
from .weather_tool.weather_spec import WEATHER_SPEC


def _register(spec, handler):
    function = spec["function"]
    validate = validate_call(handler)
    tool_stage = f"tool:{function['name']}"

    @wraps(handler)
    def invoke(*args, **kwargs):
        report_stage(tool_stage, STAGE_LABELS.get(tool_stage, '正在调用工具…'))
        try:
            result = validate(*args, **kwargs)
        except ValidationError:
            log_event('tool_failure', tool=function['name'], reason='argument_validation_failed')
            raise
        check_cancelled()
        report_stage('generating')
        return result

    # JSON Schema 用于模型绑定，Python 函数签名用于校验实际参数。
    return StructuredTool.from_function(
        func=invoke,
        name=function["name"],
        description=function["description"],
        args_schema=function["parameters"],
        infer_schema=False,
    )


CHAT_TOOLS = [
    _register(CURRENT_TIME_SPEC, get_current_time),
    _register(WEATHER_SPEC, get_weather),
]
