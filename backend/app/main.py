import os
import sys
# 获取当前目录路径
current_dir = os.path.dirname((os.path.abspath(__file__)))
sys.path.append(current_dir)

from core.app import create_app
from core.config import VERSION
from routers import chat, persona, model, misc, history, version
from static.mount import NoCacheStaticFiles

from config import configer



# 创建应用
app = create_app(VERSION)


# 添加路由
# get
app.include_router(persona.router)
app.include_router(misc.router)
app.include_router(version.router)

# post
app.include_router(chat.router)
app.include_router(model.router)
app.include_router(history.router)

# 挂载静态资源
front_dir = os.path.join(
    os.path.dirname(os.path.abspath(configer.root_dir_path)),
    "frontend"
)
app.mount("/", NoCacheStaticFiles(directory=front_dir, html=True), name="static")
