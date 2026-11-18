import logging
from pathlib import Path

import uvicorn

from app.config import configer
from app.account_logging import logger, reset_active_logs
from app.accounts.files import UserDataPaths
from app.system_config import load_system_config


STARTUP_MESSAGES = (
    'Started server process ',
    'Waiting for application startup.',
    'Application startup complete.',
    'Application startup failed.',
    'Uvicorn running on ',
)


def main():
    server = load_system_config(migrate=True).server
    # 在 Uvicorn 写出启动消息前清空上次运行的当前日志。
    reset_active_logs(Path(configer.log_save_path), UserDataPaths())
    logging.basicConfig(handlers=logger.handlers, level=logging.WARNING, force=True)
    logging.captureWarnings(True)
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    console.addFilter(lambda record: record.getMessage().startswith(STARTUP_MESSAGES))
    logging.getLogger('uvicorn.error').addHandler(console)
    uvicorn.run('app.main:app', host=server.host, port=server.port, workers=1, log_config=None)


if __name__ == '__main__':
    main()
