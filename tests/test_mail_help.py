"""Почта для входа: страница говорит, чего именно не хватает, даёт ссылку на пароль приложения и умеет проверять отправку."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_mailhelp_test.db"

import db  # noqa: E402
import mailer  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

ENV_BASE = {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "me@gmail.com", "ADMIN_EMAIL": "me@gmail.com",
}
GONE = ("SMTP_PASSWORD", "SMTP_PORT", "SMTP_FROM")


def env(**extra):
    base = {k: v for k, v in os.environ.items() if k not in GONE}
    base.update(ENV_BASE)
    base.update(extra)
    return mock.patch.dict(os.environ, base, clear=True)


class MailerHelpTest(unittest.TestCase):
    def test_missing_names_only(self):
        with env():
            self.assertEqual(mailer.missing(), ["SMTP_PASSWORD"])
        with env(SMTP_PASSWORD="x"):
            self.assertEqual(mailer.missing(), [])
            self.assertTrue(mailer.configured())

    def test_password_help_by_provider(self):
        for host, part in (("smtp.gmail.com", "myaccount.google.com/apppasswords"), ("smtp.yandex.ru", "id.yandex.ru"),
                           ("smtp.mail.ru", "account.mail.ru")):
            with env(SMTP_HOST=host):
                self.assertIn(part, mailer.password_help()[1])
        with env(SMTP_HOST="smtp.example.org"):
            self.assertIsNone(mailer.password_help())

    def test_reason_in_plain_words(self):
        import smtplib
        self.assertIn("пароль приложения", mailer.explain(smtplib.SMTPAuthenticationError(535, b"bad")))
        self.assertIn("соединиться", mailer.explain(TimeoutError()))
        self.assertIn("соединиться", mailer.explain(OSError("x")))
        self.assertIn("ошибка", mailer.explain(ValueError("x")).lower())


class SettingsMailTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.h = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def page(self):
        r = await self.client.get("/portal/settings", headers=self.h)
        return await r.text()

    async def test_shows_what_is_missing_with_links(self):
        with env():
            t = await self.page()
        self.assertIn("SMTP_PASSWORD", t)
        self.assertIn("https://myaccount.google.com/apppasswords", t)
        self.assertIn("настройках хостинга", t)
        self.assertIn("Переменные / Secrets", t)
        self.assertNotIn("railway.com", t.lower())
        self.assertNotIn('value="mailtest"', t)       # проверять нечем, пока нет пароля

    async def test_configured_offers_test_and_reports_reason(self):
        with env(SMTP_PASSWORD="secret-pass"):
            t = await self.page()
            self.assertIn('value="mailtest"', t)
            self.assertNotIn("secret-pass", t)       # значения переменных на страницу не попадают
            self.assertNotIn("SMTP_PASSWORD</code>", t)
            with mock.patch.object(mailer, "send_checked", mock.AsyncMock(return_value=(False, "не принят пароль"))):
                r = await self.client.post("/portal/settings", data={"step": "mailtest"}, headers=self.h, allow_redirects=False)
            self.assertEqual(r.status, 303)
            self.assertIn("%D0%BD%D0%B5+%D0%BF%D1%80%D0%B8%D0%BD%D1%8F%D1%82", r.headers["Location"])   # «не принят»
            with mock.patch.object(mailer, "send_checked", mock.AsyncMock(return_value=(True, ""))) as m:
                r = await self.client.post("/portal/settings", data={"step": "mailtest"}, headers=self.h, allow_redirects=False)
            self.assertEqual(m.await_args.args[0], "me@gmail.com")


if __name__ == "__main__":
    unittest.main()
