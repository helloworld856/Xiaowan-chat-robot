from os.path import join, abspath, dirname


class Config():
    # 配置根目录的绝对路径
    root_dir_path = dirname(abspath('..config'))

    # 日志配置
    log_save_path = join(root_dir_path, 'log_config/app.log')

    # 压缩对话阈值，输入总token数大于这个阈值就压缩历史对话
    compress_history_threshold = 100000

    # tokenizer配置
    tokenizer_path = join(root_dir_path, 'utils/deepseek_tokenizer')

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

    # 数据库配置
    host = "localhost"
    user = 'root'
    port = 3306
    charset = 'utf8mb4'

    # 接口配置
    # 监听
    listen_host = "127.0.0.1"
    # listen_host = "0.0.0.0"
    listen_port = 8000


configer = Config()

