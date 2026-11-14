import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402

GOOD = {"service_id": "1", "name": "Анна", "phone": "8 900 777-88-99", "address": "Москва, ул. Ленина, 1",
        "when": "завтра в 10:00", "pet": "Дымок, лабрадор", "comment": "домофон 12",
        "consent": "on", "site": ""}


class ValidateTest(unittest.TestCase):
    def test_good(self):
        clean, errors = webform.validate(dict(GOOD))
        self.assertEqual(errors, {})
        self.assertEqual(clean["phone"], "+79007778899")
        self.assertNotIn("contact", clean)  # способ связи больше не спрашиваем

    def test_requires_consent_phone_name_address(self):
        for field in ("consent", "phone", "name", "address", "service_id"):
            d = dict(GOOD)
            d.pop(field)
            _, errors = webform.validate(d)
            self.assertIn(field, errors, field)

    def test_bad_phone(self):
        clean, errors = webform.validate(dict(GOOD, phone="123"))
        self.assertIn("phone", errors)

    def test_limits_length_and_strips(self):
        d = dict(GOOD, comment="я" * 5000, name="  Анна  ")
        clean, _ = webform.validate(d)
        self.assertEqual(len(clean["comment"]), 500)
        self.assertEqual(clean["name"], "Анна")

    def test_honeypot(self):
        _, errors = webform.validate(dict(GOOD, site="http://spam"))
        self.assertIn("bot", errors)

    def test_escape(self):
        html = webform.render_form([], {"name": '"><script>alert(1)</script>'}, {})
        self.assertNotIn("<script>alert", html)


class HttpTest(unittest.TestCase):
    def run_app(self, coro):
        async def go():
            original_config = db.config
            with tempfile.TemporaryDirectory() as directory:
                db.config = replace(original_config, db_path=str(Path(directory) / "webform.db"))
                try:
                    await db.init_db()
                    sent = []

                    async def fake(bot, text, reply_markup=None, telegram_only=False):
                        sent.append(text)
                        return True

                    async def fake_client_notify(*args, **kwargs):
                        return True

                    orig = orders.notify_admins
                    orig_client_notify = orders.notify
                    orders.notify_admins = fake
                    orders.notify = fake_client_notify
                    try:
                        app = webserver.make_app()
                        app["bot"] = object()
                        async with TestClient(TestServer(app)) as c:
                            return await coro(c, sent)
                    finally:
                        orders.notify_admins = orig
                        orders.notify = orig_client_notify
                finally:
                    db.config = original_config
        return asyncio.run(go())

    def test_form_page_and_submit(self):
        async def case(c, sent):
            r = await c.get("/request")
            self.assertEqual(r.status, 200)
            html = await r.text()
            for word in ("Выгул", "Зооняня", "Передержка", "Зоотакси", "/policy"):
                self.assertIn(word, html)
            self.assertNotIn("http://", html.replace("http://www.w3.org", ""))  # ни одного внешнего ресурса
            r = await c.post("/request", data=GOOD)
            self.assertEqual(r.status, 200)
            self.assertIn("Заявка принята", await r.text())
            self.assertEqual(len(sent), 1)
            self.assertIn("+79007778899", sent[0])
            self.assertIn("Заявка с сайта", sent[0])
            row = await db.fetch_one("SELECT * FROM web_requests")
            self.assertEqual(row["phone"], "+79007778899")
            self.assertEqual(row["status"], "new")
            notification = await db.fetch_one(
                "SELECT web_request_id, status FROM web_request_notifications")
            self.assertEqual((notification["web_request_id"], notification["status"]), (row["id"], "sent"))
        self.run_app(case)

    def test_outbox_insert_failure_rolls_back_accepted_lead(self):
        async def case(c, sent):
            await db.execute(
                """CREATE TRIGGER reject_lead_notification
                   BEFORE INSERT ON web_request_notifications
                   BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
            response = await c.post("/request", data=GOOD)
            self.assertEqual(response.status, 500)
            self.assertEqual(await db.fetch_one("SELECT id FROM web_requests"), None)
            self.assertEqual(await db.fetch_one("SELECT id FROM web_request_notifications"), None)
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_post_commit_lead_read_failure_still_returns_accepted(self):
        async def case(c, sent):
            original_fetch = db.fetch_one

            async def fail_lead_read(sql, params=()):
                if "FROM web_requests r" in sql and "JOIN services" in sql:
                    raise RuntimeError("sensitive-token-must-not-be-logged")
                return await original_fetch(sql, params)

            with mock.patch.object(db, "fetch_one", side_effect=fail_lead_read), \
                    self.assertLogs("webform", level="ERROR") as captured:
                response = await c.post("/request", data=GOOD)
            self.assertEqual(response.status, 200)
            self.assertIn("Заявка принята", await response.text())
            request = await db.fetch_one("SELECT id FROM web_requests")
            notification = await db.fetch_one(
                "SELECT status, attempts, last_error FROM web_request_notifications")
            self.assertIsNotNone(request)
            self.assertEqual(notification["status"], "pending")
            self.assertEqual(notification["attempts"], 1)
            self.assertEqual(notification["last_error"], "RuntimeError")
            self.assertNotIn("sensitive-token", "\n".join(captured.output))
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_invalid_shows_errors_and_keeps_values(self):
        async def case(c, sent):
            r = await c.post("/request", data=dict(GOOD, phone="1"))
            self.assertEqual(r.status, 400)
            t = await r.text()
            self.assertIn("номер", t.lower())
            self.assertIn("Анна", t)
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_rate_limit(self):
        async def case(c, sent):
            webform.RATE.clear()
            codes = [(await c.post("/request", data=GOOD)).status for _ in range(webform.RATE_LIMIT + 2)]
            self.assertEqual(codes[:webform.RATE_LIMIT], [200] * webform.RATE_LIMIT)
            self.assertEqual(codes[-1], 429)
        self.run_app(case)


if __name__ == "__main__":
    unittest.main()
