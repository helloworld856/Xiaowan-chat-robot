from pathlib import Path


# 从文件位置定位部署资源，使启动入口不依赖终端的工作目录。
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
SOFTWARE_NAME = "小晚 AI 角色对话软件"
VERSION = "1.0.0"


class Config:
    # 配置根目录的绝对路径
    root_dir_path = str(BACKEND_ROOT)

    # 日志配置
    log_save_path = str(BACKEND_ROOT / 'log_config' / 'app.log')

    # tokenizer配置
    tokenizer_path = str(BACKEND_ROOT / 'utils' / 'deepseek_tokenizer')

    # 回复配置
    ANALYSIS_DEFAULT_RESULT = {  # 未分析出结果时的默认分析结果
        "intent": "一般对话",
        "emotion": "无情感波动",
        "answer": "日常互动",
    }

    GENERATE_DEFAULT_RESULT = {  # json解析失败时的默认回复
        "reply": ["嗯...我刚刚有点没组织好语言，你再说一遍嘛"],
        "inner monologue": [],
        "emotion": "困惑",
        "action": "",
    }


configer = Config()
