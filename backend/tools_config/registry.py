from langchain_core.tools import StructuredTool
from pydantic import validate_call

from .time_tool.time import get_current_time
from .time_tool.time_spec import CURRENT_TIME_SPEC

from .weather_tool.weather import get_weather
from .weather_tool.weather_spec import WEATHER_SPEC



def _register(spec, handler):
    function = spec["function"]
    # JSON Schema 用于模型绑定，Python 函数签名用于校验实际参数。
    return StructuredTool.from_function(
        func=validate_call(handler),
        name=function["name"],
        description=function["description"],
        args_schema=function["parameters"],
        infer_schema=False,
    )


CHAT_TOOLS = [
    _register(CURRENT_TIME_SPEC, get_current_time),
    _register(WEATHER_SPEC, get_weather),
]
