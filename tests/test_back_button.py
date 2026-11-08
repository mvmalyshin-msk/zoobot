"""Кнопка «Назад» (стрелка) есть на каждой странице глубже стартовой и нет на самой стартовой (главная и список заявок)."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_back_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import brand  # noqa: E402
import db  # noqa: E402
import webchat  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402

BACK = re.compile(r'<a[^>]*class="back(?: compact)?"[^>]*href="([^"]+)"|<a[^>]*href="([^"]+)"[^>]*class="back(?: compact)?"')


class BackButtonTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.admin = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def back_of(self, path, headers=None):
        r = await self.client.get(path, headers=headers or {})
        t = await r.text()
        m = BACK.search(t)
        return (m.group(1) or m.group(2)) if m else None, t

    def test_helper_text(self):
        h = brand.back("/portal")
        self.assertIn('class="back"', h)
        self.assertIn('href="/portal"', h)
        self.assertIn("←", h)
        self.assertIn("Назад", h)
        self.assertIn("history.back()", h)       # возвращает туда, откуда пришли, а ссылка — запасной путь
        self.assertIn("document.referrer.indexOf(location.origin)===0", h)  # «назад» только на свой сайт
        self.assertNotIn("javascript:", h)

    async def test_client_pages_have_back(self):
        for path in ("/map", "/request", "/policy", "/chat/login?t=abc", "/cabinet", "/chat/login?t=abc&n=portal"):
            href, _ = await self.back_of(path)
            self.assertEqual(href, "/", path)

    async def test_done_page_has_back(self):
        self.assertIn('class="back"', webform.render_done(5))

    async def test_chat_has_back(self):
        href, _ = await self.back_of("/chat", self.admin)
        self.assertEqual(href, "/")

    async def test_portal_subpages_have_back_to_portal(self):
        for path in ("/portal/clients", "/portal/executors", "/portal/settings", "/portal/tariffs", "/portal/bot"):
            href, _ = await self.back_of(path, self.admin)
            self.assertEqual(href, "/portal", path)

    async def test_portal_login_has_back_home(self):
        href, _ = await self.back_of("/portal")
        self.assertEqual(href, "/")

    async def test_admin_can_reach_home_from_every_portal_page(self):
        for path in ("/portal", "/portal/clients", "/portal/executors", "/portal/settings", "/portal/tariffs", "/portal/bot"):
            _, t = await self.back_of(path, self.admin)
            self.assertRegex(t, r'<a[^>]*class="brand"[^>]*href="/"', path)    # на главную ведёт надпись ЗООКОНСЬЕРЖ

    async def test_start_pages_have_no_back(self):
        for path, hdr in (("/", None), ("/portal", self.admin)):
            href, _ = await self.back_of(path, hdr)
            self.assertIsNone(href, path)


if __name__ == "__main__":
    unittest.main()
