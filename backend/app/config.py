from pathlib import Path


# 从文件位置定位部署资源，使启动入口不依赖终端的工作目录。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SOFTWARE_NAME = "小晚 AI 角色对话软件"
VERSION = "1.0.0"
SYSTEM_LOG_PATH = BACKEND_ROOT / 'log_config' / 'app.log'
UTILS_PATH = BACKEND_ROOT / 'utils'
PROMPTS_PATH = BACKEND_ROOT / 'app' / 'prompts'

