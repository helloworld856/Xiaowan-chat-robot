import re
from pathlib import Path

from starlette.staticfiles import StaticFiles


# Vite 默认在文件名末尾追加 8 个 URL 安全字符作为构建哈希。
_HASHED_ASSET = re.compile(r'[^/]+-[A-Za-z0-9_-]{8}\.[^/]+')
_CACHEABLE_MEDIA_DIRS = frozenset(('avatars', 'ico'))


class SiteStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        parts = Path(path).parts
        if (
            response.status_code in (200, 304) and len(parts) == 2
            and parts[0] == 'assets' and _HASHED_ASSET.fullmatch(parts[1])
        ):
            response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        elif response.status_code in (200, 304) and parts and parts[0] in _CACHEABLE_MEDIA_DIRS:
            # 头像与图标允许浏览器缓存并配合 ETag 校验，避免每次刷新或打开资料弹窗重复拉取。
            response.headers['Cache-Control'] = 'public, max-age=86400, must-revalidate'
        else:
            response.headers.update({
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0',
            })
        return response
