"""«Войти через Telegram»: сайт показывает кнопку-ссылку на бота, бот подтверждает, браузер входит под этим клиентом."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_tg_login_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402


class TgLoginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM pending_logins")
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()

    async def start(self):
        r = await self.client.get("/chat/login/tg")
        t = await r.text()
        return r, t, re.search(r"start=lg_([\w-]+)", t).group(1)

    async def poll(self, token):
        return await self.client.get(f"/chat/login/tg/poll?t={token}")

    async def test_page_has_bot_link_and_login_page_offers_it(self):
        r, t, token = await self.start()
        self.assertEqual(r.status, 200)
        self.assertIn("https://t.me/", t)
        self.assertIn("/chat/login/tg/poll", t)
        self.assertIn('href="/chat/login/tg"', await (await self.client.get("/chat/login")).text())

    async def test_waiting_then_confirmed_logs_in_as_that_client(self):
        _, _, token = await self.start()
        j = await (await self.poll(token)).json()
        self.assertEqual(j, {"ok": False})
        self.assertTrue(await webchat.confirm_tg_login(token, 555))
        r = await self.poll(token)
        self.assertEqual((await r.json())["ok"], True)
        cookie = r.cookies[webchat.COOKIE].value
        row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(cookie),))
        self.assertEqual(row["uid"], 555)
        self.assertEqual((await (await self.poll(token)).json())["ok"], False)      # одноразово

    async def test_after_login_chat_and_cabinet_open(self):
        _, _, token = await self.start()
        await webchat.confirm_tg_login(token, 556)
        r = await self.poll(token)
        cookie = {webchat.COOKIE: r.cookies[webchat.COOKIE].value}
        for path in ("/chat", "/cabinet"):
            g = await self.client.get(path, cookies=cookie, allow_redirects=False)
            self.assertEqual(g.status, 200, f"{path}: {g.status} {g.headers.get('Location')}")

    async def test_logged_in_client_skips_login_pages(self):
        _, _, token = await self.start()
        await webchat.confirm_tg_login(token, 557)
        r = await self.poll(token)
        cookie = {webchat.COOKIE: r.cookies[webchat.COOKIE].value}
        for path in ("/chat/login", "/chat/login/tg"):
            g = await self.client.get(path, cookies=cookie, allow_redirects=False)
            self.assertEqual((g.status, g.headers.get("Location")), (302, "/chat"), path)   # уже вошёл: сразу в кабинет
        self.client.session.cookie_jar.clear()
        g = await self.client.get("/chat/login", allow_redirects=False)
        self.assertEqual(g.status, 200)                                                    # гостю — форма входа

    async def test_plain_guest_session_still_sees_login_form(self):
        self.client.session.cookie_jar.clear()
        await self.client.get("/chat")                                      # браузер получил гостевую сессию, но не входил
        for path in ("/chat/login", "/chat/login/tg"):
            g = await self.client.get(path, allow_redirects=False)
            self.assertEqual(g.status, 200, path)                          # «Войти как клиент» открывает вход, а не чат гостя

    async def test_unknown_token_admin_and_expired_refused(self):
        self.assertFalse(await webchat.confirm_tg_login("nope", 555))
        _, _, token = await self.start()
        self.assertFalse(await webchat.confirm_tg_login(token, 111))                # админ входит через портал
        j = await (await self.poll(token)).json()
        self.assertEqual((j["ok"], j.get("admin")), (False, True))                  # страница узнаёт причину и объясняет её
        _, _, token2 = await self.start()
        await db.execute("UPDATE pending_logins SET exp=0 WHERE k=?", ("tg:" + webchat._hash(token2),))
        self.assertFalse(await webchat.confirm_tg_login(token2, 555))
        self.assertEqual((await (await self.poll("garbage")).json())["ok"], False)


class AdminExplained(unittest.TestCase):
    def test_constant_and_page_script(self):
        self.assertIn("администратора", webchat.ADMIN_AS_CLIENT_TEXT)
        import inspect
        self.assertIn("j.admin", inspect.getsource(webchat.page_login_tg))
        self.assertIn("ADMIN_AS_CLIENT_TEXT", inspect.getsource(webchat.page_login_tg))


if __name__ == "__main__":
    unittest.main()


class PhoneFormOrderTest(unittest.TestCase):
    def test_telegram_button_comes_first_and_code_hint_is_honest(self):
        import webchat
        t = webchat._phone_form().text
        self.assertLess(t.index("/chat/login/tg"), t.index("/chat/login/phone"))   # самый надёжный путь — первым
        self.assertIn("Код придёт в Telegram только", t)                           # честно: когда код не придёт
        self.assertIn("если номер уже есть у нас", t)
