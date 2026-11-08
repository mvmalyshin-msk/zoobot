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
        webchat._tg_logins.clear()
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

    async def test_unknown_token_admin_and_expired_refused(self):
        self.assertFalse(await webchat.confirm_tg_login("nope", 555))
        _, _, token = await self.start()
        self.assertFalse(await webchat.confirm_tg_login(token, 111))                # админ входит через портал
        j = await (await self.poll(token)).json()
        self.assertEqual((j["ok"], j.get("admin")), (False, True))                  # страница узнаёт причину и объясняет её
        _, _, token2 = await self.start()
        webchat._tg_logins[token2]["exp"] = 0
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
