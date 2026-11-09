from pydantic import BaseModel
from settings_config import ModelSettings

class ModelConfigRequest(ModelSettings):
    pass


class ModelConfigResponse(BaseModel):
    status: bool  # 是否验证成功
    info: str     # 提示信息
