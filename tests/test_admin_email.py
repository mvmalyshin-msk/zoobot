"""Вход администратора по почте: адрес из настроек проекта (ADMIN_EMAIL) → код письмом → вход в портал.
Адрес в тесте вымышленный: настоящий хранится только в переменных проекта."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_admin_email_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import mailer  # noqa: E402
import portal  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
ADMIN = min(config.admin_ids)


def run(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM kv WHERE key LIKE 'admin_email%'")
        portal._hits.clear()
        portal._fails.clear()
        return await fn()
    return LOOP.run_until_complete(go())


class AdminEmailFromProjectTest(unittest.TestCase):
    def test_railway_email_belongs_to_the_admin_and_is_normalised(self):
        async def go():
            with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "  Owner@Example.COM "}):
                return await portal.admin_email(ADMIN), await portal.admin_email(ADMIN + 1)
        mine, other = run(go)
        self.assertEqual(mine, "owner@example.com")
        self.assertIsNone(other)

    def test_email_confirmed_in_portal_overrides_railway(self):
        async def go():
            await db.execute("INSERT INTO kv (key, value) VALUES (?, ?)", (f"admin_email:{ADMIN}", "new@example.com"))
            with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "owner@example.com"}):
                return await portal.admin_email(ADMIN)
        self.assertEqual(run(go), "new@example.com")


class EmailLoginFlowTest(unittest.TestCase):
    def test_code_by_email_lets_the_admin_into_the_portal(self):
        sent = []

        async def fake_send(to, subject, body):
            sent.append((to, subject))
            return True

        async def go():
            app = webserver.make_app()
            app["bot"] = None  # в проде бот кладётся при запуске; для входа по почте он не нужен
            async with TestClient(TestServer(app)) as c:
                with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "owner@example.com"}), \
                        mock.patch.object(mailer, "configured", return_value=True), \
                        mock.patch.object(mailer, "send", side_effect=fake_send):
                    stranger = await (await c.post("/portal/code", data={"via": "email", "email": "x@example.com"})).text()
                    none_sent = len(sent)
                    page = await (await c.post("/portal/code", data={"via": "email", "email": "Owner@Example.com"})).text()
                code = sent[-1][1].rsplit(" ", 1)[-1] if sent else ""
                r = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
                return stranger, none_sent, page, sent, r.status, r.headers.get("Location")
        stranger, none_sent, page, sent, status, location = run(go)
        self.assertEqual(none_sent, 0)                       # на чужой адрес письмо не уходит
        self.assertIn("код уже в почте", stranger)           # и ответ тот же — по нему не угадать адрес
        self.assertIn("код уже в почте", page)
        self.assertEqual(sent[-1][0], "owner@example.com")
        self.assertEqual((status, location), (303, "/portal/face"))  # ключей Face ID ещё нет — сразу предлагаем

    def test_without_mail_server_the_page_says_what_is_missing(self):
        async def go():
            app = webserver.make_app()
            app["bot"] = None  # в проде бот кладётся при запуске; для входа по почте он не нужен
            async with TestClient(TestServer(app)) as c:
                with mock.patch.dict(os.environ, {"ADMIN_EMAIL": "owner@example.com"}), \
                        mock.patch.object(mailer, "configured", return_value=False):
                    return await (await c.post("/portal/code", data={"via": "email", "email": "owner@example.com"})).text()
        self.assertIn("Почта для входа не настроена", run(go))


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
