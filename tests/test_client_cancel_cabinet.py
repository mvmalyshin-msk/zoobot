"""Личный кабинет: клиент отменяет любую заявку, которую кабинет ему показывает.
Кабинет перечисляет заявки по двум владельцам — сессия сайта и её подтверждённый Telegram, —
а отмена раньше сверяла только сессию: заявка «по Telegram» показывалась с кнопкой, но давала «Заявка не найдена»."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_client_cancel_cabinet_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

TG = 777001


def run(coro):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_requests")
        sent = []

        async def fake_admins(bot, text, reply_markup=None, **kw):
            sent.append(text)

        async def fake_notify(bot, tg_id, text, reply_markup=None):
            return True
        saved = (orders.notify_admins, orders.notify)
        orders.notify_admins, orders.notify = fake_admins, fake_notify
        try:
            app = webserver.make_app()
            app["bot"] = object()
            async with TestClient(TestServer(app)) as c:
                return await coro(c, sent)
        finally:
            orders.notify_admins, orders.notify = saved
    return asyncio.run(go())


async def new_request(uid):
    svc = await db.fetch_one("SELECT id FROM services WHERE active=1 LIMIT 1")
    return await db.execute(
        "INSERT INTO web_requests (service_id, name, phone, address, pet, web_uid) VALUES (?,?,?,?,?,?)",
        (svc["id"], "Анна", "+79007778899", "Москва", "Дымок", uid))


class CancelInCabinet(unittest.TestCase):
    async def logged_in(self, c):
        """Сайтовая сессия-гость, подтверждённая на Telegram-клиента TG."""
        tok = await webchat.new_session()
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
        await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (?, 'Анна', '+79007778899')", (TG,))
        await db.execute("INSERT OR IGNORE INTO users (tg_id, name) VALUES (?, 'Гость')", (uid,))
        await db.execute("UPDATE users SET verified_tg_id=? WHERE tg_id=?", (TG, uid))
        webchat._tg_cache.clear()
        c.session.cookie_jar.update_cookies({webchat.COOKIE: tok})
        return uid

    def test_request_owned_by_verified_telegram_can_be_cancelled(self):
        async def case(c, sent):
            uid = await self.logged_in(c)
            rid = await new_request(TG)
            html = await (await c.get("/cabinet")).text()
            self.assertIn(f"/request/cancel/{rid}", html)               # кабинет её показывает…
            r = await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertEqual(r.status, 302, await r.text())            # …и отмена срабатывает
            row = await db.fetch_one("SELECT status, admin_note FROM web_requests WHERE id=?", (rid,))
            self.assertEqual(row["status"], "rejected")
            self.assertEqual(row["admin_note"], "Отменена клиентом")
            self.assertEqual(len(sent), 1)
            self.assertIsNotNone(uid)
        run(case)

    def test_request_of_the_session_itself_still_works(self):
        async def case(c, sent):
            uid = await self.logged_in(c)
            rid = await new_request(uid)
            r = await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertEqual(r.status, 302)
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (rid,)))["status"], "rejected")
        run(case)

    def test_someone_elses_request_is_still_refused(self):
        async def case(c, sent):
            await self.logged_in(c)
            rid = await new_request(TG + 1)
            r = await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertEqual(r.status, 302)                              # в кабинет с понятным сообщением
            self.assertIn("/cabinet?m=", r.headers["Location"])
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (rid,)))["status"], "new")
            self.assertEqual(sent, [])
        run(case)

    def test_cancel_error_is_a_cabinet_page_not_bare_text(self):
        async def case(c, sent):
            r = await c.post("/request/cancel/1", allow_redirects=False)     # без входа
            self.assertEqual(r.status, 401)
            t = await r.text()
            self.assertIn("<!doctype html>", t)
            self.assertIn("Войти", t)
        run(case)


class BehindProxy(unittest.TestCase):
    """За прокси хостинга Host может быть внутренним, а браузер шлёт Origin с публичным адресом сайта."""

    def _cancel_with_origin(self, headers):
        from config import config
        async def case(c, sent):
            tok = await webchat.new_session()
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
            c.session.cookie_jar.update_cookies({webchat.COOKIE: tok})
            rid = await new_request(uid)
            r = await c.post(f"/request/cancel/{rid}", headers=headers, allow_redirects=False)
            return r.status, (await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (rid,)))["status"]
        old = config.webapp_url
        object.__setattr__(config, "webapp_url", "https://zoo.example.test")
        try:
            return run(case)
        finally:
            object.__setattr__(config, "webapp_url", old)

    def test_public_origin_is_accepted_when_host_is_internal(self):
        status, st = self._cancel_with_origin({"Origin": "https://zoo.example.test"})
        self.assertEqual((status, st), (302, "rejected"))

    def test_forwarded_host_is_accepted(self):
        status, st = self._cancel_with_origin({"Origin": "https://front.example.test", "X-Forwarded-Host": "front.example.test"})
        self.assertEqual((status, st), (302, "rejected"))

    def test_foreign_origin_is_still_refused(self):
        status, st = self._cancel_with_origin({"Origin": "https://evil.example.org"})
        self.assertEqual((status, st), (403, "new"))


if __name__ == "__main__":
    unittest.main()
