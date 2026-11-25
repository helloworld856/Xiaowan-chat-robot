from functools import wraps

from langchain_core.tools import StructuredTool
from pydantic import ValidationError, validate_call

from app.account_logging import log_event

from .time_tool.time import get_current_time
from .time_tool.time_spec import CURRENT_TIME_SPEC

from .weather_tool.weather import get_weather
from .weather_tool.weather_spec import WEATHER_SPEC



def _register(spec, handler):
    function = spec["function"]
    validate = validate_call(handler)

    @wraps(handler)
    def invoke(*args, **kwargs):
        try:
            return validate(*args, **kwargs)
        except ValidationError:
            log_event('tool_failure', tool=function['name'], reason='argument_validation_failed')
            raise

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
