"""Клиент может отменить свою заявку с сайта: статус меняется, админ получает уведомление и видит «Отменена клиентом»."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_request_cancel_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import portal  # noqa: E402
import webserver  # noqa: E402
from tests.test_webform import GOOD  # noqa: E402


class CancelRequest(unittest.TestCase):
    def run_app(self, coro):
        async def go():
            await db.init_db()
            await db.execute("DELETE FROM web_requests")
            import webform
            webform.RATE.clear()
            sent = []

            async def fake_admins(bot, text, reply_markup=None):
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

    async def submit(self, c):
        await c.post("/request", data=GOOD)
        return (await db.fetch_one("SELECT id FROM web_requests ORDER BY id DESC LIMIT 1"))["id"]

    def test_done_page_offers_cancel(self):
        async def case(c, sent):
            html = await (await c.post("/request", data=GOOD)).text()
            rid = (await db.fetch_one("SELECT id FROM web_requests"))["id"]
            self.assertIn(f"/request/cancel/{rid}", html)
            self.assertIn("Отменить заявку", html)
        self.run_app(case)

    def test_owner_cancels_status_changes_and_admin_is_told(self):
        async def case(c, sent):
            rid = await self.submit(c)
            sent.clear()
            r = await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertIn(r.status, (200, 302, 303))
            row = await db.fetch_one("SELECT * FROM web_requests WHERE id=?", (rid,))
            self.assertEqual(row["status"], "rejected")
            self.assertIn("Отменена клиентом", row["admin_note"])
            self.assertEqual(len(sent), 1)
            self.assertIn(f"отменил заявку №{rid}", sent[0])
        self.run_app(case)

    def test_stranger_cannot_cancel_someone_elses_request(self):
        async def case(c, sent):
            rid = await self.submit(c)
            c.session.cookie_jar.clear()
            r = await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertIn(r.status, (401, 403, 404))
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (rid,)))["status"], "new")
        self.run_app(case)

    def test_finished_request_cannot_be_cancelled(self):
        async def case(c, sent):
            rid = await self.submit(c)
            await db.execute("UPDATE web_requests SET status='done' WHERE id=?", (rid,))
            sent.clear()
            await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (rid,)))["status"], "done")
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_second_cancel_does_not_notify_twice(self):
        async def case(c, sent):
            rid = await self.submit(c)
            await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            sent.clear()
            await c.post(f"/request/cancel/{rid}", allow_redirects=False)
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_cabinet_lists_requests_with_cancel_button(self):
        async def case(c, sent):
            rid = await self.submit(c)
            html = await (await c.get("/cabinet")).text()
            self.assertIn(f"/request/cancel/{rid}", html)
        self.run_app(case)

    def test_portal_shows_who_cancelled(self):
        row = {"id": 7, "status": "rejected", "admin_note": "Отменена клиентом", "name": "Анна", "phone": "+7",
               "when_text": "", "address": "", "created_at": "2026-11-09 10:00:00", "service": "Выгул", "category": "walk"}
        self.assertIn("Отменена клиентом", portal.request_label(row))
        row["admin_note"] = "дубль"
        self.assertNotIn("клиентом", portal.request_label(row))


if __name__ == "__main__":
    unittest.main()
