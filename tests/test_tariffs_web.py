"""Страница «Тарифы» в портале администратора: начальная стоимость услуг, ставки зоотакси, наценки и надбавки (%, ₽)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_tariffs_web_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import pricing  # noqa: E402
import taxi  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402


class TariffsWebTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.cookie = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def get(self, path="/portal/tariffs", **kw):
        r = await self.client.get(path, headers=self.cookie, **kw)
        return r.status, await r.text()

    async def post(self, data, headers=None, follow=False):
        return await self.client.post("/portal/tariffs", data=data, headers=self.cookie if headers is None else headers, allow_redirects=follow)

    async def price(self, code="walk30"):
        return (await db.fetch_one("SELECT base_price FROM services WHERE code=?", (code,)))["base_price"]

    async def test_page_needs_admin(self):
        r = await self.client.get("/portal/tariffs")
        t = await r.text()
        self.assertNotIn("Начальная стоимость", t)
        self.assertNotIn("Наценк", t)

    async def test_page_shows_prices_taxi_and_markup(self):
        st, t = await self.get()
        self.assertEqual(st, 200)
        for must in ("Начальная стоимость", "Ставки зоотакси", "Наценка сервиса", "Ночное время", 'name="step" value="price"',
                     'name="step" value="taxi"', 'name="step" value="rule"'):
            self.assertIn(must, t)
        for s in await db.fetch_all("SELECT name FROM services WHERE active=1"):
            self.assertIn(s["name"], t)

    async def test_menu_has_tariffs_button(self):
        st, t = await self.get("/portal")
        self.assertIn('href="/portal/tariffs"', t)
        self.assertIn(">Тарифы<", t)

    async def test_change_start_price(self):
        sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
        old = await self.price()
        r = await self.post({"step": "price", "id": str(sid), "price": "777"})
        self.assertEqual(r.status, 303)
        self.assertEqual(await self.price(), 777)
        for bad in ("0", "-5", "abc", "99999999", "", "12.5", "nan"):
            await self.post({"step": "price", "id": str(sid), "price": bad})
            self.assertEqual(await self.price(), 777, bad)
        await db.execute("UPDATE services SET base_price=? WHERE id=?", (old, sid))

    async def test_change_taxi_rate(self):
        await self.post({"step": "taxi", "name": "км", "value": "17"})
        self.assertEqual((await taxi.get_tariff()).per_km, 17)
        await self.post({"step": "taxi", "name": "км", "value": "9999"})
        self.assertEqual((await taxi.get_tariff()).per_km, 17)
        await self.post({"step": "taxi", "name": "нет_такой", "value": "5"})
        self.assertEqual((await taxi.get_tariff()).per_km, 17)
        await self.post({"step": "taxi", "name": "км", "value": "13"})

    async def test_change_rule_value_and_switch(self):
        night = await db.fetch_one("SELECT id, value FROM price_rules WHERE condition_type='night' AND service_id IS NULL")
        await self.post({"step": "rule", "id": str(night["id"]), "value": "45", "active": "1"})
        row = await db.fetch_one("SELECT value, active FROM price_rules WHERE id=?", (night["id"],))
        self.assertEqual((row["value"], row["active"]), (45, 1))
        await self.post({"step": "rule", "id": str(night["id"]), "value": "45"})  # галочка снята — правило выключено
        self.assertEqual((await db.fetch_one("SELECT active FROM price_rules WHERE id=?", (night["id"],)))["active"], 0)
        await self.post({"step": "rule", "id": str(night["id"]), "value": "-3", "active": "1"})
        await self.post({"step": "rule", "id": str(night["id"]), "value": "900", "active": "1"})  # процент больше 300 — отказ
        self.assertEqual((await db.fetch_one("SELECT value FROM price_rules WHERE id=?", (night["id"],)))["value"], 45)
        await db.execute("UPDATE price_rules SET value=?, active=1 WHERE id=?", (night["value"], night["id"]))

    async def test_markup_applies_to_every_price(self):
        sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
        base, _ = await pricing.calculate_price(sid, {"quantity": 1})
        mk = await db.fetch_one("SELECT id, value FROM price_rules WHERE condition_type='always'")
        self.assertEqual(mk["value"], 0)  # по умолчанию наценки нет: цены клиентов не меняются
        await self.post({"step": "rule", "id": str(mk["id"]), "value": "10", "active": "1"})
        total, parts = await pricing.calculate_price(sid, {"quantity": 1})
        self.assertEqual(total, base + round(base * 0.10))
        self.assertTrue(any("Наценка сервиса" in n for n, _ in parts))
        await self.post({"step": "rule", "id": str(mk["id"]), "value": "0", "active": "1"})
        self.assertEqual((await pricing.calculate_price(sid, {"quantity": 1}))[0], base)

    async def test_post_protected(self):
        sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
        before = await self.price()
        r = await self.post({"step": "price", "id": str(sid), "price": "5"}, headers={})
        self.assertEqual(r.status, 403)
        r = await self.post({"step": "price", "id": str(sid), "price": "5"}, headers={**self.cookie, "Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)
        self.assertEqual(await self.price(), before)


if __name__ == "__main__":
    unittest.main()
