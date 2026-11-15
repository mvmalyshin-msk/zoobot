"""Аудит: на каждой внутренней странице публичного сайта есть кнопка «Назад» (правило проекта), на главной её нет."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_public_back_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402

INNER = ["/contact", "/o-nas", "/policy", "/stati", "/goroda", "/peredergka", "/vygul-sobak", "/zoonyanya", "/zootaksi",
         "/vygul-sobak/himki", "/peredergka/lobnya", "/zootaksi/zelenograd", "/request", "/request?service=taxi",
         "/request?service=walk", "/stati/kak-vybrat-peredergku"]


class PublicBack(unittest.TestCase):
    def test_inner_pages_have_back_and_home_does_not(self):
        async def go():
            await db.init_db()
            app = webserver.make_app()
            app["bot"] = object()
            async with TestClient(TestServer(app)) as c:
                for path in INNER:
                    r = await c.get(path)
                    self.assertEqual(r.status, 200, path)
                    self.assertIn('class="back"', await r.text(), path)
                home = await (await c.get("/")).text()
                self.assertNotIn('class="back"', home)
            await db.close_shared()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
