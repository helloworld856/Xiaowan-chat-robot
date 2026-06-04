"""
版本 & env
"""

import time
from dotenv import load_dotenv
from log_config import logger

load_dotenv()

VERSION = f"v{int(time.time())}"
logger.info(f"version:{VERSION}")
