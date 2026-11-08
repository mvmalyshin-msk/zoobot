"""Тарифы по каждой услуге отдельно: своя цена, включение, своя наценка и свои надбавки, которые ЗАМЕНЯЮТ общие."""
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_tariffs_service_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import pricing  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

NIGHT = datetime(2030, 1, 2, 20, 30, tzinfo=timezone.utc)  # 23:30 по Москве


class ServiceTariffTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM price_rules WHERE service_id IS NOT NULL")
        await db.execute("UPDATE price_rules SET active=1, value=30 WHERE condition_type='night' AND service_id IS NULL")
        await db.execute("UPDATE price_rules SET active=1, value=0 WHERE condition_type='always' AND service_id IS NULL")
        await db.execute("UPDATE services SET active=1")
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.h = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}
        self.walk = await db.fetch_one("SELECT * FROM services WHERE code='walk30'")
        self.other = await db.fetch_one("SELECT * FROM services WHERE code='walk60'")
        self.days = await db.fetch_one("SELECT * FROM services WHERE unit='сутки' LIMIT 1")

    async def asyncTearDown(self):
        await db.execute("DELETE FROM price_rules WHERE service_id IS NOT NULL")
        await db.execute("UPDATE services SET active=1")
        await self.client.close()

    async def post(self, sid, data):
        return await self.client.post(f"/portal/tariffs/{sid}", data=data, headers=self.h, allow_redirects=False)

    async def price(self, svc, when=None):
        return (await pricing.calculate_price(svc["id"], {"quantity": 1, "start_at": when}))[0]

    async def test_page_and_link_from_list(self):
        r = await self.client.get("/portal/tariffs", headers=self.h)
        self.assertIn(f'href="/portal/tariffs/{self.walk["id"]}"', await r.text())
        r = await self.client.get(f"/portal/tariffs/{self.walk['id']}", headers=self.h)
        t = await r.text()
        self.assertEqual(r.status, 200)
        for must in (self.walk["name"], 'name="step" value="svc"', 'name="step" value="srule"', "Наценка", "Ночное время", "Заказ на сегодня"):
            self.assertIn(must, t)
        self.assertIn('href="/portal/tariffs"', t)
        t_days = await (await self.client.get(f"/portal/tariffs/{self.days['id']}", headers=self.h)).text()
        self.assertNotIn("Ночное время", t_days)    # для суточных услуг «ночь» не применяется

    async def test_unknown_service_and_no_admin(self):
        self.assertEqual((await self.client.get("/portal/tariffs/99999", headers=self.h)).status, 404)
        r = await self.client.post(f"/portal/tariffs/{self.walk['id']}", data={"step": "svc", "price": "1"}, allow_redirects=False)
        self.assertEqual(r.status, 403)

    async def test_price_and_switch_off(self):
        await self.post(self.walk["id"], {"step": "svc", "price": "555", "active": "1"})
        self.assertEqual(await self.price(self.walk), 555)
        await self.post(self.walk["id"], {"step": "svc", "price": "555"})          # галочки нет — услуга выключена
        with self.assertRaises(ValueError):
            await self.price(self.walk)
        self.assertEqual((await db.fetch_one("SELECT active FROM services WHERE id=?", (self.walk["id"],)))["active"], 0)
        await self.post(self.walk["id"], {"step": "svc", "price": "0", "active": "1"})
        self.assertEqual((await db.fetch_one("SELECT base_price FROM services WHERE id=?", (self.walk["id"],)))["base_price"], 555)

    async def test_own_markup_only_for_this_service(self):
        base_w, base_o = await self.price(self.walk), await self.price(self.other)
        await self.post(self.walk["id"], {"step": "srule", "condition": "always", "kind": "percent", "value": "20", "active": "1"})
        self.assertEqual(await self.price(self.walk), base_w + round(base_w * 0.2))
        self.assertEqual(await self.price(self.other), base_o)

    async def test_own_surcharge_replaces_global_one(self):
        base = await self.price(self.walk)
        self.assertEqual(await self.price(self.walk, NIGHT), base + round(base * 0.30))    # общая ночная +30%
        await self.post(self.walk["id"], {"step": "srule", "condition": "night", "kind": "percent", "value": "50", "active": "1"})
        self.assertEqual(await self.price(self.walk, NIGHT), base + round(base * 0.50))    # своя +50% вместо, а не вместе
        other = await self.price(self.other)
        self.assertEqual(await self.price(self.other, NIGHT), other + round(other * 0.30))  # у остальных общая

    async def test_own_rule_switched_off_cancels_global(self):
        base = await self.price(self.walk)
        await self.post(self.walk["id"], {"step": "srule", "condition": "night", "kind": "percent", "value": "30"})  # без галочки
        self.assertEqual(await self.price(self.walk, NIGHT), base)

    async def test_fixed_kind_and_reset(self):
        base = await self.price(self.walk)
        await self.post(self.walk["id"], {"step": "srule", "condition": "night", "kind": "fixed", "value": "75", "active": "1"})
        self.assertEqual(await self.price(self.walk, NIGHT), base + 75)
        await self.post(self.walk["id"], {"step": "sreset", "condition": "night"})
        self.assertEqual(await self.price(self.walk, NIGHT), base + round(base * 0.30))    # снова общая
        self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM price_rules WHERE service_id=?", (self.walk["id"],)))["n"], 0)

    async def test_update_not_duplicate(self):
        for v in ("10", "12"):
            await self.post(self.walk["id"], {"step": "srule", "condition": "urgent_today", "kind": "fixed", "value": v, "active": "1"})
        rows = await db.fetch_all("SELECT value FROM price_rules WHERE service_id=? AND condition_type='urgent_today'", (self.walk["id"],))
        self.assertEqual([r["value"] for r in rows], [12])

    async def test_bad_input_rejected(self):
        for data in ({"condition": "night", "kind": "percent", "value": "-5"}, {"condition": "night", "kind": "percent", "value": "9999"},
                     {"condition": "night", "kind": "fixed", "value": "999999999"}, {"condition": "hack", "kind": "percent", "value": "5"},
                     {"condition": "night", "kind": "weird", "value": "5"}, {"condition": "night", "kind": "percent", "value": "abc"}):
            await self.post(self.walk["id"], {"step": "srule", "active": "1", **data})
        self.assertEqual((await db.fetch_one("SELECT COUNT(*) AS n FROM price_rules WHERE service_id=?", (self.walk["id"],)))["n"], 0)
        await self.post(self.walk["id"], {"step": "svc", "price": "-1", "active": "1"})
        await self.post(self.walk["id"], {"step": "svc", "price": "x", "active": "1"})
        self.assertGreater(await self.price(self.walk), 0)


if __name__ == "__main__":
    unittest.main()
