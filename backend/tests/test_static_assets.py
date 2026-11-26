import tempfile
import unittest
from pathlib import Path

import _imports
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette

from app.static_assets import SiteStaticFiles


class StaticAssetsTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_successful_hashed_assets_are_immutable(self):
        immutable = 'public, max-age=31536000, immutable'
        media_cache = 'public, max-age=86400, must-revalidate'
        hashed = 'assets/index-AB_ab-12.js'
        cacheable_media = ['avatars/avatar.png', 'avatars/system/cat.png', 'ico/arrow.png']
        no_store_paths = ['index.html', 'assets/static.js']
        with tempfile.TemporaryDirectory() as folder:
            for name in [hashed, *cacheable_media, *no_store_paths, '404.html']:
                file = Path(folder) / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text('test', encoding='utf-8')
            app = Starlette()
            app.mount('/', SiteStaticFiles(directory=folder, html=True))
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get(f'/{hashed}')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], immutable)
                cached = await client.get(f'/{hashed}', headers={'if-none-match': response.headers['etag']})
                self.assertEqual(cached.status_code, 304)
                self.assertEqual(cached.headers['cache-control'], immutable)

                for name in cacheable_media:
                    with self.subTest(path=name):
                        response = await client.get(f'/{name}')
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.headers['cache-control'], media_cache)
                        cached = await client.get(f'/{name}', headers={'if-none-match': response.headers['etag']})
                        self.assertEqual(cached.status_code, 304)
                        self.assertEqual(cached.headers['cache-control'], media_cache)

                for name in ['', *no_store_paths]:
                    with self.subTest(path=name):
                        response = await client.get(f'/{name}')
                        self.assertEqual(response.status_code, 200)
                        self.assertIn('no-store', response.headers['cache-control'])
                for missing in ('/assets/missing-12345678.js', '/avatars/missing.png'):
                    response = await client.get(missing)
                    self.assertEqual(response.status_code, 404)
                    self.assertIn('no-store', response.headers['cache-control'])
