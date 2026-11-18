import logging
from pathlib import Path

import uvicorn

from app.config import configer
from app.account_logging import configure_logging, logger, reset_active_logs
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
    config = load_system_config(migrate=True)
    configure_logging(config.logging)
    # 启用清理时，在 Uvicorn 写出启动消息之前处理上次运行的日志。
    if config.logging.clear_on_startup:
        reset_active_logs(Path(configer.log_save_path), UserDataPaths())
    logging.basicConfig(handlers=logger.handlers, level=logging.WARNING, force=True)
    logging.captureWarnings(True)
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    console.addFilter(lambda record: record.getMessage().startswith(STARTUP_MESSAGES))
    logging.getLogger('uvicorn.error').addHandler(console)
    uvicorn.run('app.main:app', host=config.server.host, port=config.server.port, workers=1, log_config=None)


if __name__ == '__main__':
    main()
