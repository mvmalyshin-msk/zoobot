"""Статусы заявок в портале администратора (замечание владельца 8 ноября 2026): отмену клиентом видно сразу,
у каждой заявки свой цвет: новая — белая, в работе — зелёная, выполнена — тёмно-серая с ярко-зелёной галочкой.
Плюс зоотакси «от 450 ₽»."""
import asyncio
import os
import re
import secrets
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_portal_status_test.db"

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import pricing  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=getattr(method, "chat_id", 0) or 0, type="private"))


async def make_order(status, actor="admin:111", cancelled_now=False):
    tg = 300_000_000 + secrets.randbelow(10**7) * 10
    u = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, 'Клиент', '+79001234567', 1)", (tg,))
    sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
    oid = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, status, created_at) "
                           "VALUES (?,?, '2030-01-01 10:00:00', 910, ?, datetime('now'))", (u, sid, status))
    if status != "created":
        await db.execute("INSERT INTO order_statuses_log (order_id, from_status, to_status, actor) VALUES (?,?,?,?)",
                         (oid, "created", status, actor))
    return oid


def run(fn):
    async def go():
        await db.init_db()
        bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, None
        async with TestClient(TestServer(app)) as c:
            tok = await webchat.new_session(111)
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
            return await fn(c)
    return LOOP.run_until_complete(go())


def row(html, oid):
    return re.search(rf'<a class="item card ord[^"]*" href="/portal/order/{oid}">.*?</a>', html, re.S).group(0)


class RowColours(unittest.TestCase):
    def test_new_is_white_working_green_done_dark_with_green_tick(self):
        async def t(c):
            new, paid, prog = await make_order("created"), await make_order("paid"), await make_order("in_progress")
            done = await make_order("completed")
            html = await (await c.get("/portal?f=all")).text()
            self.assertIn("st-new", row(html, new).split('href=')[0])
            self.assertIn("st-work", row(html, paid).split('href=')[0])
            self.assertIn("st-work", row(html, prog).split('href=')[0])
            d = row(html, done)
            self.assertIn("st-done", d.split('href=')[0])
            self.assertRegex(d, r'<i class="tick">✓</i>')
        run(t)

    def test_cancelled_row_is_marked(self):
        async def t(c):
            oid = await make_order("cancelled", actor="client:5")
            html = await (await c.get("/portal?f=all")).text()
            r = row(html, oid)
            self.assertIn("st-cancelled", r.split('href=')[0])
            self.assertIn("Отменил клиент", r)
        run(t)

    def test_order_page_has_status_class_and_tick(self):
        async def t(c):
            oid = await make_order("completed")
            html = await (await c.get(f"/portal/order/{oid}")).text()
            self.assertRegex(html, r'<div class="stbar st-done">')
            self.assertIn('<i class="tick">✓</i>', html)
        run(t)


class CancelIsVisible(unittest.TestCase):
    def test_recent_cancellation_stays_in_default_list(self):
        async def t(c):
            oid = await make_order("cancelled", actor="client:5")
            html = await (await c.get("/portal")).text()   # фильтр по умолчанию «В работе»
            self.assertIn(f"/portal/order/{oid}", html)
        run(t)

    def test_old_cancellation_leaves_default_list(self):
        async def t(c):
            oid = await make_order("cancelled", actor="client:5")
            await db.execute("UPDATE order_statuses_log SET created_at=datetime('now','-3 days') WHERE order_id=?", (oid,))
            html = await (await c.get("/portal")).text()
            self.assertNotIn(f'/portal/order/{oid}"', html)
        run(t)

    def test_summary_reports_last_status_change(self):
        async def t(c):
            oid = await make_order("cancelled", actor="client:5")
            j = await (await c.get("/portal/api/summary")).json()
            self.assertGreater(j["last_status"], 0)
            self.assertEqual(j["status_order"], oid)
            self.assertIn("Отменён", j["status_text"])
            self.assertIn("клиент", j["status_text"])
        run(t)

    def test_page_script_watches_status_changes(self):
        async def t(c):
            html = await (await c.get("/portal")).text()
            self.assertIn("last_status", html)
        run(t)


class TaxiMinimum(unittest.TestCase):
    def test_taxi_never_cheaper_than_450(self):
        async def t():
            await db.init_db()
            sid = (await db.fetch_one("SELECT id FROM services WHERE code='taxi'"))["id"]
            total, bd = await pricing.calculate_price(sid, {"quantity": 1})
            return total, bd
        total, bd = LOOP.run_until_complete(t())
        self.assertEqual(total, 450)
        self.assertEqual(sum(a for _, a in bd), 450)

    def test_taxi_catalog_from_450(self):
        async def t():
            await db.init_db()
            items = await db.fetch_all("SELECT * FROM services WHERE code='taxi'")
            return (await pricing.with_client_price(items))[0]["from_price"]
        self.assertEqual(LOOP.run_until_complete(t()), 450)


if __name__ == "__main__":
    unittest.main()
