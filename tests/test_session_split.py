"""Админ и клиент не сливаются в одной учётной записи браузера.
Раньше и чат клиента, и портал жили на одной cookie: вход в портал в соседней вкладке подменял клиента
админом, и в клиентском чате (синяя лента) появлялись код входа и меню администратора (скриншот владельца)."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_session_split_test.db"
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
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
ADMIN = min(config.admin_ids)
BLUE, RED = "#1d6fd6", "#d62b2b"


def run(fn):
    async def go():
        await db.init_db()
        for t in ("web_sessions", "web_events", "web_login"):
            await db.execute(f"DELETE FROM {t}")
        webchat._linked_cache.clear()
        webchat._targets_cache.clear()
        portal._hits.clear()
        portal._fails.clear()
        app = webserver.make_app()
        app["bot"] = None
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return LOOP.run_until_complete(go())


def _set_cookies(r) -> str:
    return " ".join(r.headers.getall("Set-Cookie", []))


def _cookies(client_token=None, admin_token=None) -> dict:
    parts = []
    if client_token:
        parts.append(f"{webchat.COOKIE}={client_token}")
    if admin_token:
        parts.append(f"{webchat.ADMIN_COOKIE}={admin_token}")
    return {"Cookie": "; ".join(parts)} if parts else {}


class SeparateCookiesTest(unittest.TestCase):
    def test_portal_login_sets_admin_cookie_and_keeps_client_cookie(self):
        async def go(c):
            code = await portal._issue_code(ADMIN, "tg")
            r = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
            return r.status, _set_cookies(r)
        status, cookies = run(go)
        self.assertEqual(status, 303)
        self.assertIn(f"{webchat.ADMIN_COOKIE}=", cookies)
        self.assertNotIn(f"{webchat.COOKIE}=", cookies)

    def test_client_and_admin_in_one_browser_each_see_only_their_own(self):
        async def go(c):
            client_tg = 7901
            ct = await webchat.new_session(client_tg)
            at = await webchat.new_session(ADMIN)
            await webchat.push(client_tg, "send", who="bot", text="сообщение клиенту")
            await webchat.push(ADMIN, "send", who="bot", text="🔐 Код входа в портал: 123456")
            h = _cookies(ct, at)
            chat = await (await c.get("/chat/api/history", headers=h)).json()
            adm = await (await c.get("/portal/bot/api/history", headers=h)).json()
            return chat, adm
        chat, adm = run(go)
        chat_text = " ".join(m.get("text", "") for m in chat["messages"])
        adm_text = " ".join(m.get("text", "") for m in adm["messages"])
        self.assertIn("сообщение клиенту", chat_text)
        self.assertNotIn("Код входа", chat_text)
        self.assertFalse(chat["admin"])
        self.assertIn("Код входа", adm_text)
        self.assertNotIn("сообщение клиенту", adm_text)

    def test_client_chat_never_acts_as_admin_even_with_old_cookie(self):
        async def go(c):
            old = await webchat.new_session(ADMIN)  # старая cookie чата, выданная админу до разделения
            await webchat.push(ADMIN, "send", who="bot", text="админское меню")
            api = await c.get("/chat/api/history", headers=_cookies(old))
            page = await c.get("/chat", headers=_cookies(old))
            return api.status, await page.text(), _set_cookies(page)
        api_status, page, cookies = run(go)
        self.assertEqual(api_status, 401)
        self.assertIn(BLUE, page)
        self.assertNotIn(RED, page)
        self.assertIn(f"{webchat.COOKIE}=", cookies)  # выдана новая клиентская (гостевая) сессия


class AdminBotChatTest(unittest.TestCase):
    def test_admin_bot_chat_lives_in_portal_and_needs_admin_cookie(self):
        async def go(c):
            anon = await c.get("/portal/bot", allow_redirects=False)
            client = await c.get("/portal/bot", headers=_cookies(await webchat.new_session(7902)), allow_redirects=False)
            at = await webchat.new_session(ADMIN)
            ok = await c.get("/portal/bot", headers=_cookies(admin_token=at))
            return anon.status, client.status, ok.status, await ok.text()
        anon, client, ok, page = run(go)
        self.assertIn(anon, (302, 303))
        self.assertIn(client, (302, 303))
        self.assertEqual(ok, 200)
        self.assertIn(RED, page)
        self.assertNotIn(BLUE, page)
        self.assertIn("/portal/bot/api/", page)
        self.assertNotIn("/chat/api/", page)
        self.assertNotIn('href="/cabinet"', page)

    def test_client_cookie_does_not_open_admin_api(self):
        async def go(c):
            r = await c.get("/portal/bot/api/history", headers=_cookies(await webchat.new_session(ADMIN)))
            return r.status
        self.assertEqual(run(go), 401)

    def test_portal_bot_link_goes_to_admin_chat(self):
        out = portal.page("Заявки", "<p>x</p>")
        self.assertIn('href="/portal/bot"', out)
        self.assertNotIn('href="/chat"', out)


class OneTimeLinksTest(unittest.TestCase):
    def _login(self, tg, nxt):
        async def go(c):
            url = await webchat.make_login_link(tg, nxt)
            t = url.split("t=", 1)[1].split("&", 1)[0]
            r = await c.post("/chat/login", data={"t": t, "n": "portal" if nxt == "portal" else "chat"},
                             allow_redirects=False)
            return r.headers.get("Location"), _set_cookies(r)
        return run(go)

    def test_admin_link_sets_admin_cookie(self):
        loc, cookies = self._login(ADMIN, "portal")
        self.assertEqual(loc, "/portal")
        self.assertIn(f"{webchat.ADMIN_COOKIE}=", cookies)
        self.assertNotIn(f"{webchat.COOKIE}=", cookies)

    def test_client_link_sets_client_cookie(self):
        loc, cookies = self._login(7903, "chat")
        self.assertEqual(loc, "/chat")
        self.assertIn(f"{webchat.COOKIE}=", cookies)
        self.assertNotIn(f"{webchat.ADMIN_COOKIE}=", cookies)

    def test_admin_using_a_chat_link_lands_in_admin_chat(self):
        loc, cookies = self._login(ADMIN, "chat")
        self.assertEqual(loc, "/portal/bot")
        self.assertIn(f"{webchat.ADMIN_COOKIE}=", cookies)
        self.assertNotIn(f"{webchat.COOKIE}=", cookies)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
