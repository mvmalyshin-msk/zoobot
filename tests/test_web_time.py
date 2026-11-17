"""Время заявки с сайта: 2 часа на подбор, «Экспресс», график админов — на сервере, по единым правилам timerules."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_web_time_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from config import config  # noqa: E402
import webform  # noqa: E402


def at(h, m=0, days=0):
    n = datetime.now(config.tz).replace(hour=h, minute=m, second=0, microsecond=0)
    return n + timedelta(days=days)


def form(when, **kw):
    base = {"service_id": "1", "name": "Анна", "phone": "8 926 417-35-82", "address": "Химки, улица Победы, 3",
            "pet": "Рекс", "consent": "on", "service": "walk",
            "when_date": when.strftime("%Y-%m-%d"), "when_time": when.strftime("%H:%M")}
    base.update(kw)
    return base


def run(when, now, **kw):
    with mock.patch.object(webform, "_now", return_value=now):
        return webform.validate(form(when, **kw))


class WebTime(unittest.TestCase):
    def test_far_enough_is_accepted(self):
        clean, errors = run(at(16, 0), at(12, 0))
        self.assertNotIn("when_time", errors)
        self.assertEqual(clean["express"], False)

    def test_too_soon_without_express_explains_and_offers_express(self):
        _, errors = run(at(12, 30), at(12, 0))
        self.assertIn("when_time", errors)
        self.assertIn("минимум 2 часа", errors["when_time"])
        self.assertIn("«Экспресс»", errors["when_time"])

    def test_express_lets_soon_orders_through_in_working_hours(self):
        clean, errors = run(at(12, 30), at(12, 0), express="on")
        self.assertNotIn("when_time", errors)
        self.assertTrue(clean["express"])

    def test_express_does_not_help_at_night(self):
        _, errors = run(at(11, 0, days=1), at(23, 0), express="on")
        self.assertNotIn("when_time", errors)  # 11:00 завтра — это и есть ближайшее время
        _, errors = run(at(6, 0, days=1), at(23, 0), express="on")
        self.assertIn("when_time", errors)
        self.assertIn("с 09:00 утра", errors["when_time"])

    def test_express_ticked_at_night_is_not_stored(self):
        clean, errors = run(at(11, 0), at(3, 10), express="on")
        self.assertNotIn("when_time", errors)
        self.assertFalse(clean["express"])
        self.assertNotIn("Экспресс", clean["when"])

    def test_night_order_for_six_morning_gets_example_one(self):
        _, errors = run(at(6, 0), at(3, 10))
        self.assertIn("11:00", errors["when_time"])

    def test_out_of_service_hours(self):
        _, errors = run(at(23, 0), at(8, 0))
        self.assertIn("Выгул собак доступен с 06:00 до 22:00", errors["when_time"])

    def test_time_is_required_for_non_boarding(self):
        _, errors = run(at(16, 0), at(12, 0), when_time="")
        self.assertIn("when_time", errors)

    def test_boarding_only_checks_dates(self):
        clean, errors = run(at(16, 0), at(12, 0), service="boarding", when_time="", when_to=at(16, 0, days=3).strftime("%Y-%m-%d"))
        self.assertNotIn("when_time", errors)
        self.assertFalse(clean["express"])

    def test_when_text_marks_express(self):
        clean, _ = run(at(12, 30), at(12, 0), express="on")
        self.assertIn("Экспресс", clean["when"])


class SlotsApi(unittest.IsolatedAsyncioTestCase):
    async def call(self, now, **q):
        import json
        from aiohttp.test_utils import make_mocked_request
        from urllib.parse import urlencode
        req = make_mocked_request("GET", "/api/slots?" + urlencode(q))
        with mock.patch.object(webform, "_now", return_value=now):
            resp = await webform.api_slots(req)
        return json.loads(resp.text)

    async def test_grid_marks_unavailable_and_explains(self):
        now = at(12, 0)
        r = await self.call(now, cat="walk", date=now.strftime("%Y-%m-%d"))
        by = {s["t"]: s["ok"] for s in r["slots"]}
        self.assertFalse(by["13:00"])
        self.assertTrue(by["14:00"])
        self.assertEqual(r["earliest"], "14:00")
        self.assertTrue(r["express_available"])
        self.assertIn("минимум 2 часа", r["note"])

    async def test_express_recalculates_grid_instantly(self):
        now = at(12, 0)
        r = await self.call(now, cat="walk", date=now.strftime("%Y-%m-%d"), express="1")
        by = {s["t"]: s["ok"] for s in r["slots"]}
        self.assertTrue(by["12:30"])
        self.assertFalse(by["12:00"])
        self.assertEqual(r["earliest"], "12:15")

    async def test_night_no_express_and_explanation(self):
        now = at(3, 10)
        r = await self.call(now, cat="walk", date=now.strftime("%Y-%m-%d"), express="1")
        self.assertFalse(r["express_available"])
        by = {s["t"]: s["ok"] for s in r["slots"]}
        self.assertFalse(by["06:00"])
        self.assertFalse(by["10:30"])
        self.assertTrue(by["11:00"])
        self.assertIn("с 09:00 утра", r["note"])

    async def test_bad_input_is_safe(self):
        r = await self.call(at(12, 0), cat="walk", date="не дата")
        self.assertEqual(r["slots"], [])


class ExpressSaved(unittest.TestCase):
    def test_express_request_is_stored_and_marked_for_admin(self):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        import db
        import orders
        import webserver

        async def go():
            await db.init_db()
            sent = []

            async def fake(bot, text, reply_markup=None):
                sent.append(text)

            async def no_check(*a, **k):
                return None
            orig, orig_chk = orders.notify_admins, webform.check_addresses
            orders.notify_admins, webform.check_addresses = fake, no_check
            webform.RATE.clear()
            real_now = webform._now
            try:
                app = webserver.make_app()
                app["bot"] = object()
                now = at(12, 0)
                webform._now = lambda: now
                async with TestClient(TestServer(app)) as c:
                    sid = await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1")
                    d = dict(form(at(12, 30), express="on"), service_id=str(sid["id"]))
                    r = await c.post("/request", data=d)
                    self.assertEqual(r.status, 200, await r.text())
                    row = await db.fetch_one("SELECT * FROM web_requests ORDER BY id DESC LIMIT 1")
                    self.assertEqual(row["express"], 1)
                    self.assertIn("ЭКСПРЕСС", sent[0])
                    d2 = dict(form(at(16, 0)), service_id=str(sid["id"]), nonce="n2")
                    r = await c.post("/request", data=d2)
                    self.assertEqual(r.status, 200)
                    row = await db.fetch_one("SELECT * FROM web_requests ORDER BY id DESC LIMIT 1")
                    self.assertEqual(row["express"], 0)
                    self.assertNotIn("ЭКСПРЕСС", sent[1])
            finally:
                await db.execute("DELETE FROM web_requests")
                webform._now = real_now
                orders.notify_admins, webform.check_addresses = orig, orig_chk
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
