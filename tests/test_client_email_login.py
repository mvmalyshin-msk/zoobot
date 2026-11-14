"""Вход и регистрация клиента по почте: код приходит письмом, новый адрес сразу заводит кабинет.
Адреса в тесте вымышленные."""
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_client_email_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import mailer  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

MAIL = "client@example.com"


class ClientEmailLoginTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM users WHERE email IS NOT NULL")
        await db.execute("DELETE FROM pending_logins")
        self.sent = []

        async def fake_send(to, subject, body):
            self.sent.append((to, subject, body))
            return True
        self.patches = [mock.patch.object(mailer, "configured", return_value=True),
                        mock.patch.object(mailer, "send", side_effect=fake_send)]
        for p in self.patches:
            p.start()
        app = webserver.make_app()
        app["bot"] = None
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.client.close()

    async def ask(self, email):
        return await self.client.post("/chat/login/email", data={"email": email}, headers={"Origin": self.origin})

    async def verify(self, email, code):
        return await self.client.post("/chat/login/emailcode", data={"email": email, "code": code},
                                      allow_redirects=False, headers={"Origin": self.origin})

    def code(self):
        return re.search(r"\b(\d{6})\b", self.sent[-1][2]).group(1)

    async def test_login_page_offers_every_way_in_order(self):
        t = await (await self.client.get("/chat/login")).text()
        pos = [t.find(x) for x in ('href="/chat/login/tg"', 'action="/chat/login/email"', 'action="/chat/login/phone"')]
        self.assertTrue(all(p > 0 for p in pos), pos)
        self.assertEqual(pos, sorted(pos))               # Telegram → почта → телефон

    async def test_new_email_registers_and_logs_in(self):
        r = await self.ask(MAIL)
        self.assertEqual(r.status, 200)
        self.assertEqual(self.sent[-1][0], MAIL)
        ok = await self.verify(MAIL, self.code())
        self.assertEqual((ok.status, ok.headers.get("Location")), (302, "/chat"))
        self.assertIn(webchat.COOKIE, ok.cookies)
        row = await db.fetch_one("SELECT tg_id, email FROM users WHERE email=?", (MAIL,))
        self.assertIsNotNone(row)
        self.assertLess(row["tg_id"], 0)                 # гость без Telegram

    async def test_same_email_returns_to_the_same_cabinet(self):
        await self.ask(MAIL)
        await self.verify(MAIL, self.code())
        first = (await db.fetch_one("SELECT tg_id FROM users WHERE email=?", (MAIL,)))["tg_id"]
        await db.execute("DELETE FROM pending_logins")
        await self.ask(MAIL.upper())                     # регистр не важен
        await self.verify(MAIL, self.code())
        n = (await db.fetch_one("SELECT COUNT(*) AS n FROM users WHERE email=?", (MAIL,)))["n"]
        self.assertEqual(n, 1)
        self.assertEqual((await db.fetch_one("SELECT tg_id FROM users WHERE email=?", (MAIL,)))["tg_id"], first)

    async def test_wrong_code_is_refused(self):
        await self.ask(MAIL)
        r = await self.verify(MAIL, "000000" if self.code() != "000000" else "111111")
        self.assertEqual(r.status, 200)
        self.assertNotIn(webchat.COOKIE, r.cookies)

    async def test_repeat_request_inside_a_minute_sends_no_second_letter(self):
        await self.ask(MAIL)
        await self.ask(MAIL)
        self.assertEqual(len(self.sent), 1)

    async def test_garbage_address_sends_nothing(self):
        await self.ask("not-an-email")
        self.assertEqual(self.sent, [])

    async def test_admin_address_is_not_a_client(self):
        with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "boss@example.com"}):
            await self.ask("boss@example.com")
        self.assertEqual(self.sent, [])

    async def test_without_mail_server_it_says_so(self):
        with mock.patch.object(mailer, "configured", return_value=False):
            t = await (await self.ask(MAIL)).text()
        self.assertIn("не настроена", t)
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
