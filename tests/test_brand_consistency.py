"""Единый вид: на всех страницах один шрифт (PT Sans из brand.css) и одно название «Зооконсьерж»."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_brandc_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

PUBLIC = ("/", "/map", "/request", "/policy", "/chat/login?t=abc", "/cabinet")
ADMIN = ("/portal", "/portal/clients", "/portal/executors", "/portal/settings", "/portal/tariffs", "/portal/bot", "/chat")
ROOT = Path(__file__).resolve().parent.parent


class BrandConsistencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.admin = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def pages(self):
        for p in PUBLIC:
            yield p, await (await self.client.get(p)).text()
        for p in ADMIN:
            yield p, await (await self.client.get(p, headers=self.admin)).text()
        yield "/portal(вход)", await (await self.client.get("/portal")).text()

    async def test_every_page_uses_brand_font(self):
        async for path, t in self.pages():
            self.assertIn("/brand.css", t, path)
            self.assertNotIn("-apple-system", t, f"{path}: свой шрифт вместо фирменного")

    async def test_one_name_everywhere(self):
        async for path, t in self.pages():
            self.assertNotIn("Zo Concierge", t, path)
            self.assertNotIn("ЗооТакси", t, path)
            self.assertNotIn("Зо Консьерж", t, path)

    def test_static_files_use_brand_font(self):
        for name in ("webapp/home.html", "webapp/chat.html", "webapp/index.html", "ribbons.py"):
            self.assertNotIn("-apple-system", (ROOT / name).read_text(encoding="utf-8"), name)

    def test_manifests_use_one_name(self):
        for name in ("manifest.webmanifest", "manifest-admin.webmanifest"):
            t = (ROOT / "webapp" / name).read_text(encoding="utf-8")
            self.assertIn("Зооконсьерж", t)
            self.assertIsNone(re.search(r"Zo ?Concierge", t), name)


if __name__ == "__main__":
    unittest.main()
