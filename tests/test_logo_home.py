"""Надпись «ЗООКОНСЬЕРЖ» везде кликабельна и ведёт на главную страницу; отдельной кнопки «Главная страница» больше нет."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_logo_home_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import brand  # noqa: E402
import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOGO_LINK = re.compile(r'<a[^>]*class="brand"[^>]*href="/"|<a[^>]*href="/"[^>]*class="brand"')


class LogoHomeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.admin = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    def test_helpers(self):
        self.assertRegex(brand.LOGO, LOGO_LINK)
        self.assertNotIn("Главная страница", brand.backrow("/portal"))
        self.assertIn('class="back"', brand.backrow("/portal"))
        self.assertFalse(hasattr(brand, "home"))

    async def test_client_pages_logo_links_home_without_home_button(self):
        for path in ("/request", "/policy", "/chat/login", "/chat/login/tg", "/chat", "/cabinet"):
            r = await self.client.get(path)
            if r.status != 200:
                continue
            t = await r.text()
            if "ЗОО" not in t:
                continue
            self.assertRegex(t, LOGO_LINK, path)
            self.assertNotIn("Главная страница", t, path)

    async def test_admin_pages_logo_links_home_without_home_button(self):
        for path in ("/portal", "/portal/clients", "/portal/executors", "/portal/tariffs", "/portal/settings", "/portal/bot"):
            r = await self.client.get(path, headers=self.admin)
            self.assertEqual(r.status, 200, path)
            t = await r.text()
            self.assertRegex(t, LOGO_LINK, path)
            self.assertNotIn("Главная страница", t, path)
            self.assertNotIn("🏠", t, path)

    async def test_login_pages_logo(self):
        t = await (await self.client.get("/portal")).text()          # без входа — страница входа администратора
        self.assertRegex(t, LOGO_LINK)

    async def test_home_page_logo_still_home(self):
        t = await (await self.client.get("/")).text()
        self.assertRegex(t, LOGO_LINK)


if __name__ == "__main__":
    unittest.main()
