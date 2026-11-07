"""Сохранение на экран «Домой» (iPhone): у админа своё приложение, открывает портал, а не клиентский чат.
Баг владельца: сохранил портал на экран «Домой» — открылась клиентская страница с синей лентой,
потому что у всех страниц был один manifest со start_url=/chat."""
import asyncio
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_home_screen_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import portal  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
CLIENT_MF, ADMIN_MF = 'href="/manifest.webmanifest"', 'href="/manifest-admin.webmanifest"'


def fetch(path, cookie=None):
    async def go():
        await db.init_db()
        app = webserver.make_app()
        app["bot"] = None
        async with TestClient(TestServer(app)) as c:
            headers = {"Cookie": cookie} if cookie else {}
            if cookie == "ADMIN":
                headers = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}
            r = await c.get(path, headers=headers, allow_redirects=False)
            return r.status, await r.text()
    return LOOP.run_until_complete(go())


class ManifestsTest(unittest.TestCase):
    def test_admin_app_opens_the_portal(self):
        status, body = fetch("/manifest-admin.webmanifest")
        self.assertEqual(status, 200)
        m = json.loads(body)
        self.assertEqual((m["start_url"], m["scope"], m["id"]), ("/portal", "/portal", "/portal"))
        self.assertIn("Админ", m["short_name"])

    def test_client_app_opens_the_chat(self):
        m = json.loads((ROOT / "webapp" / "manifest.webmanifest").read_text(encoding="utf-8"))
        self.assertEqual((m["start_url"], m["id"]), ("/chat", "/chat"))
        self.assertNotIn("Админ", m["short_name"])


class PagesLinkTheirOwnAppTest(unittest.TestCase):
    def test_portal_pages_and_login_are_the_admin_app(self):
        for html in (portal.page("Заявки", "<p>x</p>"), portal.login_page()):
            self.assertIn(ADMIN_MF, html)
            self.assertNotIn(CLIENT_MF, html)
            self.assertIn('apple-mobile-web-app-title" content="ЗК Админ"', html)

    def test_admin_bot_chat_is_the_admin_app_and_client_chat_is_the_client_app(self):
        _, admin = fetch("/portal/bot", "ADMIN")
        _, client = fetch("/chat")
        self.assertIn(ADMIN_MF, admin)
        self.assertNotIn(CLIENT_MF, admin)
        self.assertIn(CLIENT_MF, client)
        self.assertNotIn(ADMIN_MF, client)

    def test_client_pages_stay_the_client_app(self):
        for path in ("/cabinet", "/request"):
            _, html = fetch(path)
            self.assertIn(CLIENT_MF, html, path)
            self.assertNotIn(ADMIN_MF, html, path)

    def test_login_page_explains_signing_in_inside_the_home_screen_app(self):
        html = portal.login_page()
        self.assertIn("экрана «Домой»", html)
        self.assertIn("Прислать код в Telegram", html)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
