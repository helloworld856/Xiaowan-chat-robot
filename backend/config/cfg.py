from os.path import join, abspath, dirname


class Config():
    # 配置根目录的绝对路径
    root_dir_path = dirname(abspath('..config'))

    # 浏览器标签页图标
    page_icon = join(root_dir_path, 'web', 'page_icon', 'favicon.ico')

    # 网页文件配置
    web_file_path = join(root_dir_path, 'web/index.py')

    # 用户头像配置
    user_avatar = join(root_dir_path, 'web/fjnu.png')

    # 日志配置
    log_save_path = join(root_dir_path, 'log_config/app.log')

    # tokenizer配置
    tokenizer_path = join(root_dir_path, 'utils/deepseek_tokenizer')

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

