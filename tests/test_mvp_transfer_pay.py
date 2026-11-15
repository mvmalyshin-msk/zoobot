"""MVP, шаг 4: оплата переводом для заказа с сайта (клиент платит администратору переводом).
Цепочка: заявка → «Оформить заказ» → «Подтвердить» (клиенту уходят реквизиты и «Я оплатил») →
клиент нажимает «Я оплатил» (админу — «проверьте поступление») → админ «Оплата пришла» → заказ «оплачен».
Реквизиты — только из переменных проекта (PAY_CARD, PAY_RECIPIENT), в коде и в тестах их нет."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_mvp_transfer_pay_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import payinfo  # noqa: E402
import web_orders  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402
from handlers import client as client_h  # noqa: E402

UID = -5151
CARD = "0000 0000 0000 0000"


async def web_order():
    await db.init_db()
    await db.execute("DELETE FROM web_requests")
    await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (?, 'Анна', '+79007778899')", (UID,))
    svc = await db.fetch_one("SELECT id FROM services WHERE category='walk' AND active=1 ORDER BY base_price LIMIT 1")
    rid = await db.execute(
        "INSERT INTO web_requests (service_id, name, phone, address, pet, web_uid) VALUES (?,?,?,?,?,?)",
        (svc["id"], "Анна", "+79007778899", "Москва, Ленина 1", "Дымок, кот", UID))
    start = (datetime.now(config.tz) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
    ok, msg, oid = await web_orders.make_order(object(), rid, start, 1, actor="admin:111")
    assert ok, msg
    return oid


class TransferPayment(unittest.TestCase):
    def test_full_chain_for_web_order(self):
        os.environ["PAY_CARD"] = CARD
        os.environ["PAY_RECIPIENT"] = "Тест Т."
        client_msgs, admin_msgs = [], []

        async def fake_notify(bot, tg_id, text, reply_markup=None):
            client_msgs.append((tg_id, text, reply_markup))
            return True

        async def fake_admins(bot, text, reply_markup=None, copy_helper=True):
            admin_msgs.append(text)
        o_n, o_a = orders.notify, orders.notify_admins
        orders.notify, orders.notify_admins = fake_notify, fake_admins

        async def go():
            oid = await web_order()
            app = webserver.make_app()
            app["bot"], app["dp"] = object(), None
            async with TestClient(TestServer(app)) as c:
                tok = await webchat.new_session(111)
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
                await c.post(f"/portal/order/{oid}", data={"action": "confirm"}, allow_redirects=False)
                offer = [m for m in client_msgs if m[0] == UID and "Выберите, как удобнее оплатить" in m[1]]
                self.assertEqual(len(offer), 1, client_msgs)
                data = [b.callback_data for row in offer[0][2].inline_keyboard for b in row]
                self.assertIn(f"pay:card:{oid}", data)
                self.assertIn(f"pay:claim:{oid}", data)
                self.assertNotIn(CARD, offer[0][1])                         # реквизиты — только по нажатию «Перевод на карту»
                self.assertIn(CARD, payinfo.card_text(os.environ, oid, 1))
                await client_h._claim_paid(object(), await orders.get_order_full(oid))
                self.assertTrue(any("оплатил заказ" in t and f"№{oid}" in t for t in admin_msgs), admin_msgs)
                await c.post(f"/portal/order/{oid}", data={"action": "paid"}, allow_redirects=False)
                self.assertEqual((await db.fetch_one("SELECT status FROM orders WHERE id=?", (oid,)))["status"], "paid")
                self.assertEqual((await db.fetch_one("SELECT status FROM payments WHERE order_id=?", (oid,)))["status"], "paid")
        try:
            asyncio.run(go())
        finally:
            orders.notify, orders.notify_admins = o_n, o_a
            os.environ.pop("PAY_CARD", None)
            os.environ.pop("PAY_RECIPIENT", None)

    def test_without_requisites_nothing_is_offered(self):
        os.environ.pop("PAY_CARD", None)
        os.environ.pop("PAY_SBP_PHONE", None)
        self.assertEqual(payinfo.methods(1), [])


if __name__ == "__main__":
    unittest.main()
