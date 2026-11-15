"""Заявка на зоотакси на сайте: подсказки адреса при вводе (/api/addr) и проверка при отправке.
Адрес нельзя написать «что угодно»: он должен найтись на карте и быть выбран из подсказок или набран ровно как в подсказке.
Если карта недоступна, клиента не блокируем. Карта (geo.search) в тестах подменена."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_web_addr_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import orders  # noqa: E402
import portal  # noqa: E402
import pricing  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

TOMORROW = (datetime.now(config.tz).date() + timedelta(days=1)).isoformat()
CONTACT = {"name": "Анна", "phone": "8 900 777-88-99", "pet": "Дымок, кот", "comment": "", "consent": "on", "site": ""}


def pl(label, house=True):
    return geo.Place(label=label, district="Москва", lat=55.7, lon=37.6, outside_mkad=False, has_house=house)


A = "Ленина улица, 5, Тверской, Москва"
B = "Ленина улица, 7, Тверской, Москва"


def run(coro, search):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_requests")
        webform.RATE.clear()
        webform.SEEN.clear()
        portal._hits.clear()
        webform._addr_hits.clear()

        async def fake_notify(bot, text, reply_markup=None, **kw):
            pass
        orig = orders.notify_admins
        orders.notify_admins = fake_notify
        try:
            with mock.patch.object(geo, "search", side_effect=search):
                app = webserver.make_app()
                app["bot"] = object()
                async with TestClient(TestServer(app)) as c:
                    return await coro(c)
        finally:
            orders.notify_admins = orig
            await db.close_shared()
    return asyncio.run(go())


def const(value):
    async def f(text):
        return value
    return f


async def taxi_id():
    items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
    return str([s for s in items if s["category"] == "taxi"][0]["id"])


def body(sid, address, dest, n, **extra):
    return {**CONTACT, "service": "taxi", "service_id": sid, "address": address, "dest": dest,
            "when_date": TOMORROW, "when_time": "10:00", "nonce": n, **extra}


class Suggest(unittest.TestCase):
    def test_returns_found_addresses(self):
        async def case(c):
            d = await (await c.get("/api/addr", params={"q": "Ленина 5"})).json()
            self.assertEqual(d["items"], [A, B])
        run(case, const([pl(A), pl(B)]))

    def test_short_or_bad_query_gives_nothing_and_does_not_search(self):
        async def case(c):
            for q in ("ле", "", "ъъъъъъ ыыыы фффф"):
                d = await (await c.get("/api/addr", params={"q": q})).json()
                self.assertEqual(d["items"], [], q)
        calls = []

        async def spy(text):
            calls.append(text)
            return []
        run(case, spy)
        self.assertEqual(calls, [])

    def test_map_down_is_reported(self):
        async def case(c):
            d = await (await c.get("/api/addr", params={"q": "Ленина 5"})).json()
            self.assertEqual(d["items"], [])
            self.assertTrue(d["down"])
        run(case, const(None))

    def test_rate_limited(self):
        async def case(c):
            last = None
            for _ in range(40):
                last = await c.get("/api/addr", params={"q": "Ленина 5"})
            self.assertEqual(last.status, 429)
        run(case, const([pl(A)]))


class Submit(unittest.TestCase):
    def test_unknown_address_is_refused(self):
        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), "асдфгх 12", "Москва, Ленина 5", "n1"))
            self.assertEqual(r.status, 400)
            self.assertIn("Не нашёл такой адрес на карте", await r.text())
            self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests"))["n"], 0)
        run(case, const([]))

    def test_found_but_not_picked_asks_to_choose_and_offers_options(self):
        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), "ленина 5", "Москва, Тверская 1", "n2"))
            self.assertEqual(r.status, 400)
            t = await r.text()
            self.assertIn("Выберите адрес из подсказок", t)
            self.assertIn(f'data-pick="{A}"', t)
            self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests"))["n"], 0)
        run(case, const([pl(A), pl(B)]))

    def test_picked_addresses_are_accepted_without_search(self):
        calls = []

        async def spy(text):
            calls.append(text)
            return []

        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), A, B, "n3", address_ok=A, dest_ok=B))
            self.assertEqual(r.status, 200, await r.text())
            row = await db.fetch_one("SELECT address FROM web_requests")
            self.assertIn(A, row["address"])
            self.assertIn(B, row["address"])
        run(case, spy)
        self.assertEqual(calls, [])

    def test_typing_the_exact_suggestion_is_accepted(self):
        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), A.lower(), B, "n4"))
            self.assertEqual(r.status, 200, await r.text())
        run(case, const([pl(A), pl(B)]))

    def test_typed_house_missing_in_results_is_called_out(self):
        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), "Ленина 9", B, "n5", dest_ok=B))
            self.assertEqual(r.status, 400)
            self.assertIn("Дома «9»", await r.text())
        run(case, const([pl(A), pl(B)]))

    def test_map_down_does_not_block_the_client(self):
        async def case(c):
            r = await c.post("/request", data=body(await taxi_id(), "Москва, Ленина 1", "Химки, ветклиника", "n6"))
            self.assertEqual(r.status, 200, await r.text())
        run(case, const(None))

    def test_other_services_never_touch_the_map(self):
        async def boom(text):
            raise AssertionError("карта не нужна")

        async def case(c):
            items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
            sid = str([s for s in items if s["category"] == "walk"][0]["id"])
            r = await c.post("/request", data={**CONTACT, "service": "walk", "service_id": sid, "address": "Москва, Тверская 1",
                                               "when_date": TOMORROW, "when_time": "09:00", "nonce": "w1"})
            self.assertEqual(r.status, 200, await r.text())
        run(case, boom)


class Page(unittest.TestCase):
    def test_taxi_page_has_suggest_script_and_hidden_flags_other_pages_do_not(self):
        async def case(c):
            taxi = await (await c.get("/request?service=taxi")).text()
            self.assertIn("/api/addr", taxi)
            self.assertIn('name="address_ok"', taxi)
            self.assertIn('name="dest_ok"', taxi)
            walk = await (await c.get("/request?service=walk")).text()
            self.assertNotIn("/api/addr", walk)
        run(case, const([]))


if __name__ == "__main__":
    unittest.main()
