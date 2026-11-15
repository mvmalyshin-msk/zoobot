"""Аудит безопасности: защитные заголовки на страницах и в ответах API."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_sec_headers_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402


class SecHeaders(unittest.TestCase):
    def test_headers_on_pages_and_errors(self):
        async def go():
            await db.init_db()
            app = webserver.make_app()
            app["bot"] = object()
            async with TestClient(TestServer(app)) as c:
                for path in ("/", "/request", "/chat/login", "/portal", "/api/addr?q=x", "/net-takoy-stranicy"):
                    r = await c.get(path)
                    h = r.headers
                    self.assertEqual(h.get("X-Content-Type-Options"), "nosniff", path)
                    self.assertIn(h.get("Referrer-Policy"), ("strict-origin-when-cross-origin", "no-referrer"), path)
                    self.assertIn(h.get("X-Frame-Options"), ("SAMEORIGIN", "DENY"), path)
            await db.close_shared()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
