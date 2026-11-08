"""«Войти как клиент»: по номеру телефона. Код приходит в Telegram клиента, после входа подтягиваются его данные."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_phone_login_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

PHONE = "+79161234567"


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class PhoneLoginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM users WHERE tg_id IN (555, -77, 111)")
        await db.execute("INSERT INTO users (tg_id, tg_username, phone, phone_verified) VALUES (555, 'ivan', ?, 'telegram')", (PHONE,))
        await db.execute("INSERT INTO users (tg_id, phone) VALUES (-77, '+79990001122')")        # веб-гость без Telegram
        self.bot = FakeBot()
        app = webserver.make_app()
        app["bot"] = self.bot
        webchat._login_codes.clear()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()

    def code(self):
        return re.search(r"\b(\d{6})\b", self.bot.sent[-1][1]).group(1)

    async def ask(self, phone):
        return await self.client.post("/chat/login/phone", data={"phone": phone}, headers={"Origin": self.origin})

    async def verify(self, phone, code):
        return await self.client.post("/chat/login/code", data={"phone": phone, "code": code}, allow_redirects=False,
                                      headers={"Origin": self.origin})

    async def test_login_page_asks_phone(self):
        t = await (await self.client.get("/chat/login")).text()
        self.assertIn('action="/chat/login/phone"', t)
        self.assertIn('name="phone"', t)

    async def test_full_login_pulls_client(self):
        r = await self.ask("8 (916) 123-45-67")
        self.assertEqual(r.status, 200)
        self.assertIn('name="code"', await r.text())
        self.assertEqual(self.bot.sent[-1][0], 555)                       # код ушёл в Telegram этого клиента
        r = await self.verify("8 916 123 45 67", self.code())
        self.assertEqual(r.status, 302)
        self.assertEqual(r.headers["Location"], "/chat")
        token = r.cookies[webchat.COOKIE].value
        row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(token),))
        self.assertEqual(row["uid"], 555)                                  # данные клиента подтянуты: сессия на его id

    async def test_wrong_code_and_attempt_limit(self):
        await self.ask(PHONE)
        good = self.code()
        for _ in range(5):
            r = await self.verify(PHONE, "000000" if good != "000000" else "111111")
            self.assertEqual(r.status, 200)
        r = await self.verify(PHONE, good)                                 # после 5 ошибок код сгорает
        self.assertEqual(r.status, 200)
        self.assertNotIn(webchat.COOKIE, r.cookies)

    async def test_code_is_one_time(self):
        await self.ask(PHONE)
        c = self.code()
        self.assertEqual((await self.verify(PHONE, c)).status, 302)
        self.assertEqual((await self.verify(PHONE, c)).status, 200)

    async def test_unknown_and_guest_numbers_look_the_same_and_send_nothing(self):
        a = await (await self.ask("+79998887766")).text()
        b = await (await self.ask("+79990001122")).text()                  # гость без Telegram
        self.assertEqual(self.bot.sent, [])
        self.assertIn('name="code"', a)
        self.assertEqual(re.sub(r"value='[^']*'|value=\"[^\"]*\"", "", a), re.sub(r"value='[^']*'|value=\"[^\"]*\"", "", b))
        self.assertEqual((await self.verify("+79998887766", "123456")).status, 200)

    async def test_bad_phone_rejected_and_resend_cooldown(self):
        t = await (await self.ask("123")).text()
        self.assertIn("номер", t.lower())
        self.assertEqual(self.bot.sent, [])
        await self.ask(PHONE)
        await self.ask(PHONE)                                              # второй раз сразу — код не шлём
        self.assertEqual(len(self.bot.sent), 1)

    async def test_admin_phone_never_logs_in_as_client(self):
        await db.execute("INSERT INTO users (tg_id, phone) VALUES (111, '+79150000001')")
        await self.ask("+79150000001")
        self.assertEqual(self.bot.sent, [])

    async def test_cross_site_post_refused(self):
        r = await self.client.post("/chat/login/phone", data={"phone": PHONE}, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)


if __name__ == "__main__":
    unittest.main()
