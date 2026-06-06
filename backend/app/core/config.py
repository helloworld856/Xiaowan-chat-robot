"""
版本 & env
"""

import time
from log_config import logger



VERSION = f"v{int(time.time())}"
logger.info(f"version:{VERSION}")
