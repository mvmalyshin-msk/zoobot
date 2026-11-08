"""Панель администратора: переключатель «Заявки / Клиенты» на каждой странице, у каждой заявки номер и крупные
дата и время регистрации, переход между соседними заказами и к клиенту (жалоба владельца: «не запутаться»)."""
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
os.environ.setdefault("DB_PATH", "/tmp/zoobot_portal_panel_test.db")

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
_n = [0]


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=getattr(method, "chat_id", 0) or 0, type="private"))


async def fixtures():
    """Два заказа одного клиента (14:05 UTC = 17:05 по Москве и 09:30 UTC = 12:30) и заказ другого клиента."""
    _n[0] += 1
    tg = 200_000_000 + secrets.randbelow(10**7) * 10  # у каждого теста свой клиент, даже если база общая
    u1 = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, 'Максим', '+79778164660', 1)", (tg,))
    u2 = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, 'Ольга', '+79001112233', 1)", (tg + 1,))
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='taxi' LIMIT 1"))["id"]
    ins = "INSERT INTO orders (user_id, service_id, start_at, price_total, created_at) VALUES (?,?, '2030-01-01 10:00:00', ?, ?)"
    a = await db.execute(ins, (u1, sid, 896, "2026-11-07 09:30:00"))
    b = await db.execute(ins, (u2, sid, 700, "2026-11-08 08:00:00"))
    c = await db.execute(ins, (u1, sid, 896, "2026-11-08 14:05:12"))
    rq = await db.execute("INSERT INTO web_requests (service_id, name, phone, address, created_at) VALUES (?, 'Форма Фёдор', '+79007778899', 'Мира 1', '2026-11-08 10:15:00')", (sid,))
    return {"tg": tg, "a": a, "b": b, "c": c, "rq": rq}


def run(fn):
    async def go():
        await db.init_db()
        f = await fixtures()
        bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, None
        async with TestClient(TestServer(app)) as c:
            tok = await webchat.new_session(111)
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
            return await fn(c, f)
    return LOOP.run_until_complete(go())


class SwitchOnEveryPage(unittest.TestCase):
    def test_orders_clients_switch_is_on_each_page(self):
        async def t(c, f):
            for path in ("/portal", "/portal/clients", f"/portal/order/{f['c']}", f"/portal/request/{f['rq']}",
                         f"/portal/chat/{f['tg']}", "/portal/executors"):
                html = await (await c.get(path)).text()
                seg = re.search(r'<nav class="seg".*?</nav>', html, re.S)
                self.assertTrue(seg, path)
                self.assertIn('href="/portal"', seg.group(0), path)
                self.assertIn('href="/portal/clients"', seg.group(0), path)
        run(t)

    def test_active_tab_is_marked(self):
        async def t(c, f):
            html = await (await c.get("/portal/clients")).text()
            seg = re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)
            self.assertRegex(seg, r'class="on"[^>]*href="/portal/clients"|href="/portal/clients"[^>]*class="on"')
        run(t)

    def test_new_orders_counter_is_in_the_switch(self):
        async def t(c, f):
            html = await (await c.get("/portal")).text()
            seg = re.search(r'<nav class="seg".*?</nav>', html, re.S).group(0)
            self.assertRegex(seg, r'<b class="cnt">\d+</b>')
        run(t)


class RegistrationStamp(unittest.TestCase):
    def test_row_has_number_and_big_time_and_date(self):
        async def t(c, f):
            html = await (await c.get("/portal?f=all")).text()
            row = re.search(rf'<a class="item card ord[^"]*" href="/portal/order/{f["c"]}">.*?</a>', html, re.S).group(0)
            self.assertIn(f"№{f['c']}", row)
            self.assertRegex(row, r'<span class="stamp[^"]*"><b>17:05</b><span>08\.11</span></span>')
        run(t)

    def test_list_is_newest_first_and_grouped_by_day(self):
        async def t(c, f):
            html = await (await c.get("/portal?f=all")).text()
            self.assertLess(html.index(f'/portal/order/{f["c"]}"'), html.index(f'/portal/order/{f["a"]}"'))
            days = re.findall(r'<h3 class="day">(.*?)</h3>', html)
            self.assertIn("8 ноября", " ".join(days))
            self.assertIn("7 ноября", " ".join(days))
            self.assertLess(" ".join(days).index("8 ноября"), " ".join(days).index("7 ноября"))
        run(t)

    def test_web_request_row_has_stamp_too(self):
        async def t(c, f):
            html = await (await c.get("/portal?f=all")).text()
            row = re.search(rf'<a class="item card ord[^"]*" href="/portal/request/{f["rq"]}">.*?</a>', html, re.S).group(0)
            self.assertRegex(row, r'<span class="stamp[^"]*"><b>13:15</b><span>08\.11</span></span>')
        run(t)

    def test_order_page_shows_registration_big(self):
        async def t(c, f):
            html = await (await c.get(f"/portal/order/{f['c']}")).text()
            reg = re.search(r'<div class="reg">.*?</div>', html, re.S).group(0)
            self.assertIn("17:05", reg)
            self.assertIn("08.11.2026", reg)
            self.assertIn(f"№{f['c']}", reg)
        run(t)

    def test_request_page_shows_registration_big(self):
        async def t(c, f):
            html = await (await c.get(f"/portal/request/{f['rq']}")).text()
            reg = re.search(r'<div class="reg">.*?</div>', html, re.S).group(0)
            self.assertIn("13:15", reg)
            self.assertIn("08.11.2026", reg)
        run(t)


class SwitchingBetweenOrdersAndClients(unittest.TestCase):
    def test_order_page_has_neighbours_client_and_other_orders(self):
        async def t(c, f):
            html = await (await c.get(f"/portal/order/{f['c']}")).text()
            pager = re.search(r'<div class="pager">.*?</div>', html, re.S).group(0)
            self.assertIn(f'/portal/order/{f["b"]}', pager)      # предыдущий по номеру
            self.assertIn(f'/portal/chat/{f["tg"]}', pager)      # клиент
            others = re.search(r'<div class="others">.*?</div>', html, re.S).group(0)
            self.assertIn(f'/portal/order/{f["a"]}', others)      # другой заказ этого же клиента
            self.assertNotIn(f'/portal/order/{f["c"]}"', others)  # себя не показываем
            self.assertNotIn(f'/portal/order/{f["b"]}', others)   # чужой клиент не попадает
        run(t)

    def test_first_order_has_no_previous_link(self):
        async def t(c, f):
            first = (await db.fetch_one("SELECT MIN(id) AS n FROM orders"))["n"]
            html = await (await c.get(f"/portal/order/{first}")).text()
            pager = re.search(r'<div class="pager">.*?</div>', html, re.S).group(0)
            self.assertIn('class="off"', pager)
        run(t)

    def test_client_row_shows_last_order_with_time(self):
        async def t(c, f):
            html = await (await c.get("/portal/clients")).text()
            row = re.search(rf'<a class="item card" href="/portal/chat/{f["tg"]}">.*?</a>', html, re.S).group(0)
            self.assertIn(f"№{f['c']}", row)
            self.assertIn("08.11 17:05", row)
        run(t)


if __name__ == "__main__":
    unittest.main()
