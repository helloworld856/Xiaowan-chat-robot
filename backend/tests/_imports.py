"""Set the backend import root for unittest discovery from the repository."""

import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
# unittest 从仓库根目录发现用例时也能加载同一个 app 包。
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
