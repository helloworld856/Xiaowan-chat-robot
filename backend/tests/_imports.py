"""Set the backend import root for unittest discovery from the repository."""

import sys
from functools import cache
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
# unittest 从仓库根目录发现用例时也能加载同一个 app 包。
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@cache
def fixture_admin_hash():
    from app.accounts.auth_service import hash_password

    return hash_password('123456')


def fixture_admin_auth(**options):
    from app.accounts.auth_service import AuthService
    from app.accounts.repository import LocalAccountStore

    return AuthService(account_store=LocalAccountStore(admin_password_hash=fixture_admin_hash()), **options)
