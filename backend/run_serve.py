import logging
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from app.config import configer
from app.account_logging import logger


STARTUP_MESSAGES = (
    'Started server process ',
    'Waiting for application startup.',
    'Application startup complete.',
    'Application startup failed.',
    'Uvicorn running on ',
)


def main():
    logging.basicConfig(handlers=logger.handlers, level=logging.WARNING, force=True)
    logging.captureWarnings(True)
    logging.getLogger('uvicorn').setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
    console.addFilter(lambda record: record.getMessage().startswith(STARTUP_MESSAGES))
    logging.getLogger('uvicorn.error').addHandler(console)
    load_dotenv(Path(__file__).resolve().with_name('.env'))
    uvicorn.run('app.main:app', host=configer.listen_host, port=configer.listen_port, workers=1, log_config=None)


if __name__ == '__main__':
    main()
