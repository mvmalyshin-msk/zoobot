"""Вход по почте и паролю: письма не нужны. Регистрация и вход, пароль хранится только в виде хеша,
почта без проверки не привязывается к чужим данным, админ не входит как клиент. Адреса вымышленные."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_password_login_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import portal  # noqa: E402
import pwlogin  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

MAIL, PW = "anna@example.com", "хороший-пароль-1"


class Hashing(unittest.TestCase):
    def test_hash_is_salted_and_verifies(self):
        a, b = pwlogin.hash_password(PW), pwlogin.hash_password(PW)
        self.assertNotEqual(a, b)
        self.assertNotIn(PW, a)
        self.assertTrue(pwlogin.verify_password(PW, a))
        self.assertFalse(pwlogin.verify_password(PW + "x", a))
        self.assertFalse(pwlogin.verify_password(PW, "мусор"))


class Flow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM users WHERE login_email IS NOT NULL OR email IS NOT NULL")
        portal._hits.clear()
        portal._fails.clear()
        app = webserver.make_app()
        app["bot"] = None
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, path, **data):
        return await self.client.post(path, data=data, allow_redirects=False, headers={"Origin": self.origin})

    async def test_login_page_has_password_form(self):
        t = await (await self.client.get("/chat/login")).text()
        self.assertIn('action="/chat/login/pw"', t)
        self.assertIn('type="password"', t)

    async def test_register_creates_account_and_logs_in(self):
        r = await self.post("/chat/login/pw", mode="register", email=MAIL, password=PW)
        self.assertEqual((r.status, r.headers.get("Location")), (302, "/chat"))
        self.assertIn(webchat.COOKIE, r.cookies)
        row = await db.fetch_one("SELECT * FROM users WHERE login_email=?", (MAIL,))
        self.assertIsNotNone(row)
        self.assertLess(row["tg_id"], 0)
        self.assertNotIn(PW, row["pw_hash"])
        self.assertIsNone(row["email"])                  # непроверенная почта не записывается как подтверждённая

    async def test_login_with_right_password_and_not_with_wrong(self):
        await self.post("/chat/login/pw", mode="register", email=MAIL, password=PW)
        ok = await self.post("/chat/login/pw", mode="login", email=MAIL.upper(), password=PW)
        self.assertEqual((ok.status, ok.headers.get("Location")), (302, "/chat"))
        bad = await self.post("/chat/login/pw", mode="login", email=MAIL, password="не тот пароль")
        self.assertEqual(bad.status, 200)
        self.assertIn("Неверная почта или пароль", await bad.text())
        self.assertNotIn(webchat.COOKIE, bad.cookies)

    async def test_unknown_email_gets_same_message_as_wrong_password(self):
        r = await self.post("/chat/login/pw", mode="login", email="nobody@example.com", password=PW)
        self.assertIn("Неверная почта или пароль", await r.text())

    async def test_cannot_register_taken_email(self):
        await self.post("/chat/login/pw", mode="register", email=MAIL, password=PW)
        r = await self.post("/chat/login/pw", mode="register", email=MAIL, password="другой-пароль-2")
        self.assertEqual(r.status, 200)
        self.assertIn("уже зарегистрирована", await r.text())
        n = (await db.fetch_one("SELECT COUNT(*) AS n FROM users WHERE login_email=?", (MAIL,)))["n"]
        self.assertEqual(n, 1)

    async def test_weak_password_and_bad_email_refused(self):
        r = await self.post("/chat/login/pw", mode="register", email=MAIL, password="123")
        self.assertIn("не короче 8", await r.text())
        r = await self.post("/chat/login/pw", mode="register", email="не-почта", password=PW)
        self.assertIn("Проверьте адрес почты", await r.text())
        self.assertIsNone(await db.fetch_one("SELECT 1 FROM users WHERE login_email=?", (MAIL,)))

    async def test_admin_email_cannot_register_as_client(self):
        os.environ["ADMIN_EMAIL"] = "boss@example.com"
        try:
            r = await self.post("/chat/login/pw", mode="register", email="boss@example.com", password=PW)
            self.assertEqual(r.status, 200)
            self.assertNotIn(webchat.COOKIE, r.cookies)
        finally:
            del os.environ["ADMIN_EMAIL"]

    async def test_foreign_origin_refused(self):
        r = await self.client.post("/chat/login/pw", data={"mode": "register", "email": MAIL, "password": PW},
                                   allow_redirects=False, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)

    async def test_brute_force_is_limited(self):
        await self.post("/chat/login/pw", mode="register", email=MAIL, password=PW)
        for _ in range(12):
            r = await self.post("/chat/login/pw", mode="login", email=MAIL, password="не тот пароль")
        self.assertIn("Слишком много", await r.text())
        good = await self.post("/chat/login/pw", mode="login", email=MAIL, password=PW)
        self.assertEqual(good.status, 200)               # и верный пароль в блокировке не пускает


if __name__ == "__main__":
    unittest.main()
