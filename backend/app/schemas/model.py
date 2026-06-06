from pydantic import BaseModel

class ModelConfigRequest(BaseModel):
    model_merchant: str   # 模型厂商：deepseek / tongyi
    # api_key: str          # API密钥
    model_name: str       # 模型名称


class ModelConfigResponse(BaseModel):
    status: bool  # 是否验证成功
    info: str     # 提示信息