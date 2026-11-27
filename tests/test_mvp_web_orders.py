"""MVP, шаг 3 (решение Клода по ТЗ владельца: «сначала подтвердим цену и исполнителя»):
админ сам оформляет заказ из заявки с сайта — указывает время и количество, цену считает бот по тем же правилам,
что и в Telegram. Клиент видит статус в чате. Повторное нажатие не создаёт второй заказ. Зоотакси — только через бота
(маршрут считается по адресам)."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_mvp_web_orders_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import web_orders  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

UID = -4242


def future(days=2, hour=10):
    d = datetime.now(config.tz) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0)


async def make_request(cat="walk", pet="Дымок, кот", uid=UID):
    await db.init_db()
    await db.execute("DELETE FROM web_requests")
    await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (?, 'Анна', '+79007778899')", (uid,))
    svc = await db.fetch_one("SELECT * FROM services WHERE category=? AND active=1 ORDER BY base_price LIMIT 1", (cat,))
    rid = await db.execute(
        "INSERT INTO web_requests (service_id, name, phone, address, when_text, pet, web_uid) VALUES (?,?,?,?,?,?,?)",
        (svc["id"], "Анна", "+79007778899", "Москва, Ленина 1", "завтра", pet, uid))
    return rid, svc


def run(coro):
    async def go():
        sent = []

        async def fake_notify(bot, tg_id, text, reply_markup=None):
            sent.append((tg_id, text))
            return True
        orig = orders.notify
        orders.notify = fake_notify
        try:
            return await coro(sent)
        finally:
            orders.notify = orig
            await db.close_shared()
    return asyncio.run(go())


class MakeOrder(unittest.TestCase):

    def test_first_matching_fee_is_consumed_only_on_confirmation(self):
        async def case(sent):
            rid, _ = await make_request("boarding", uid=-4243)
            await db.execute("UPDATE web_requests SET match_tier='economy' WHERE id=?", (rid,))
            _, _, first = await web_orders.make_order(None, rid, future(), 4, "admin:1", executor_quote=3200)
            rid, _ = await make_request("boarding", uid=-4243)
            await db.execute("UPDATE web_requests SET match_tier='economy' WHERE id=?", (rid,))
            _, _, second = await web_orders.make_order(None, rid, future(), 4, "admin:1", executor_quote=3200)
            self.assertEqual((await orders.get_order_full(second))["price_total"], 3200)
            self.assertTrue((await orders.change_status(None, first, "confirmed", "admin:1"))[0])
            self.assertFalse((await orders.change_status(None, second, "confirmed", "admin:1"))[0])
            order = await orders.get_order_full(second)
            self.assertEqual((order["status"], order["price_total"], order["match_fee"]), ("created", 3299, 99))
            self.assertTrue((await orders.change_status(None, second, "confirmed", "admin:1"))[0])
            self.assertEqual((await db.fetch_one("SELECT amount FROM payments WHERE order_id=?", (second,)))["amount"], 3299)
        run(case)

    def test_express_request_is_priced_higher(self):
        async def case(sent):
            totals = []
            for express in (0, 1):
                rid, svc = await make_request()
                await db.execute("UPDATE web_requests SET express=? WHERE id=?", (express, rid))
                ok, msg, oid = await web_orders.make_order(None, rid, future(), 1, actor="admin:1")
                self.assertTrue(ok, msg)
                totals.append((await db.fetch_one("SELECT price_total FROM orders WHERE id=?", (oid,)))["price_total"])
            self.assertGreater(totals[1], totals[0])
        run(case)

    def test_creates_priced_order_linked_to_request_and_tells_client(self):
        async def case(sent):
            rid, svc = await make_request()
            ok, msg, oid = await web_orders.make_order(object(), rid, future(), 1, actor="admin:111")
            self.assertTrue(ok, msg)
            o = await db.fetch_one("SELECT * FROM orders WHERE id=?", (oid,))
            self.assertEqual(o["status"], "created")
            self.assertEqual(o["service_id"], svc["id"])
            self.assertGreater(o["price_total"], 0)
            self.assertEqual(o["address"], "Москва, Ленина 1")
            user = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (UID,))
            self.assertEqual(o["user_id"], user["id"])
            pet = await db.fetch_one("SELECT * FROM pets WHERE id=?", (o["pet_id"],))
            self.assertEqual(pet["name"], "Дымок")
            r = await db.fetch_one("SELECT * FROM web_requests WHERE id=?", (rid,))
            self.assertEqual(r["order_id"], oid)
            self.assertEqual(r["status"], "work")
            self.assertTrue(any(f"№{oid}" in t for _, t in sent))
            self.assertIsNotNone(await db.fetch_one("SELECT 1 FROM order_statuses_log WHERE order_id=?", (oid,)))
        run(case)

    def test_second_call_returns_the_same_order(self):
        async def case(sent):
            rid, _ = await make_request()
            before = (await db.fetch_one("SELECT COUNT(*) AS n FROM orders"))["n"]
            _, _, a = await web_orders.make_order(object(), rid, future(), 1, actor="admin:111")
            ok, msg, b = await web_orders.make_order(object(), rid, future(3), 1, actor="admin:111")
            self.assertEqual(a, b)
            self.assertIn("уже оформлен", msg)
            self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM orders"))["n"], before + 1)
        run(case)

    def test_past_time_is_refused(self):
        async def case(sent):
            rid, _ = await make_request()
            ok, msg, oid = await web_orders.make_order(object(), rid, future(-1), 1, actor="admin:111")
            self.assertFalse(ok)
            self.assertIsNone(oid)
            self.assertIn("прошло", msg)
        run(case)

    def test_taxi_goes_through_the_bot(self):
        async def case(sent):
            rid, _ = await make_request("taxi")
            ok, msg, oid = await web_orders.make_order(object(), rid, future(), 1, actor="admin:111")
            self.assertFalse(ok)
            self.assertIn("такси", msg.lower())
            self.assertIsNone(oid)
        run(case)

    def test_cancelled_request_cannot_become_order(self):
        async def case(sent):
            rid, _ = await make_request()
            await db.execute("UPDATE web_requests SET status='rejected' WHERE id=?", (rid,))
            ok, msg, oid = await web_orders.make_order(object(), rid, future(), 1, actor="admin:111")
            self.assertFalse(ok)
        run(case)

    def test_missing_request(self):
        async def case(sent):
            await db.init_db()
            ok, msg, oid = await web_orders.make_order(object(), 999999, future(), 1, actor="admin:111")
            self.assertFalse(ok)
        run(case)


class Portal(unittest.TestCase):
    def _app(self, coro):
        async def case(sent):
            rid, _ = await make_request()
            app = webserver.make_app()
            app["bot"], app["dp"] = object(), None
            async with TestClient(TestServer(app)) as c:
                tok = await webchat.new_session(111)
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
                return await coro(c, rid)
        return run(case)

    def test_request_page_has_make_order_form_and_action_creates_order(self):
        async def t(c, rid):
            html = await (await c.get(f"/portal/request/{rid}")).text()
            self.assertIn("Оформить заказ", html)
            self.assertIn('name="start"', html)
            when = future().strftime("%Y-%m-%dT%H:%M")
            r = await c.post(f"/portal/request/{rid}", data={"make_order": "1", "start": when, "qty": "1"},
                             allow_redirects=False)
            self.assertEqual(r.status, 303)
            oid = (await db.fetch_one("SELECT order_id FROM web_requests WHERE id=?", (rid,)))["order_id"]
            self.assertTrue(oid)
            html2 = await (await c.get(f"/portal/request/{rid}")).text()
            self.assertIn(f"/portal/order/{oid}", html2)
            self.assertNotIn('name="make_order"', html2)
        self._app(t)

    def test_bad_start_gives_message_not_order(self):
        async def t(c, rid):
            before = (await db.fetch_one("SELECT COUNT(*) AS n FROM orders"))["n"]
            r = await c.post(f"/portal/request/{rid}", data={"make_order": "1", "start": "ерунда", "qty": "1"},
                             allow_redirects=False)
            self.assertEqual(r.status, 303)
            self.assertIsNone((await db.fetch_one("SELECT order_id FROM web_requests WHERE id=?", (rid,)))["order_id"])
            self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM orders"))["n"], before)
        self._app(t)


if __name__ == "__main__":
    unittest.main()
