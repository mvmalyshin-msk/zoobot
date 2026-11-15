"""Правила выбора адреса из инструкции для такси-ассистента (владелец прислал файл, вариант 1):
не менять молча номер дома, не считать первый результат верным, предупреждать, если подача = назначению.
Без нейросети: чистые функции geo.py и проверка формы на сайте."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_address_rules_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import orders  # noqa: E402
import pricing  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402


def place(label):
    return geo.Place(label=label, district="Москва", lat=55.7, lon=37.6, outside_mkad=False, has_house=True)


class TypedHouse(unittest.TestCase):
    def test_extracts_house_as_typed(self):
        self.assertEqual(geo.typed_house("Ленина 5"), "5")
        self.assertEqual(geo.typed_house("ул. Ленина, д. 5А"), "5а")
        self.assertEqual(geo.typed_house("Ленина 5, кв. 12"), "5")
        self.assertIsNone(geo.typed_house("Ленина"))

    def test_house_of_label(self):
        self.assertEqual(geo.house_of("Ленина улица, 5А, Тверской, Москва"), "5а")
        self.assertIsNone(geo.house_of("Ленина улица, Тверской, Москва"))


class HouseNotice(unittest.TestCase):
    def test_warns_when_no_option_has_the_typed_house(self):
        n = geo.house_notice("Ленина 5", [place("Ленина улица, 7, Москва"), place("Ленина улица, 9, Москва")])
        self.assertIsNotNone(n)
        self.assertIn("5", n)

    def test_silent_when_typed_house_is_found(self):
        self.assertIsNone(geo.house_notice("Ленина 5", [place("Ленина улица, 7, Москва"), place("Ленина улица, 5, Москва")]))

    def test_5_and_5a_are_different_houses(self):
        self.assertIsNotNone(geo.house_notice("Ленина 5", [place("Ленина улица, 5А, Москва")]))

    def test_silent_when_no_house_typed(self):
        self.assertIsNone(geo.house_notice("Ленина", [place("Ленина улица, 7, Москва")]))


class SamePlace(unittest.TestCase):
    def test_same_address_detected_ignoring_case_and_spaces(self):
        self.assertTrue(geo.same_place("Москва, Ленина 1", " москва,  ленина 1 "))
        self.assertFalse(geo.same_place("Москва, Ленина 1", "Москва, Ленина 2"))
        self.assertFalse(geo.same_place("", ""))


class WebTaxiSameAddress(unittest.TestCase):
    def test_form_asks_again_when_from_equals_to(self):
        async def go():
            await db.init_db()
            await db.execute("DELETE FROM web_requests")
            webform.RATE.clear()
            webform.SEEN.clear()

            async def fake(bot, text, reply_markup=None, **kw):
                pass
            orig = orders.notify_admins
            orders.notify_admins = fake
            try:
                items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
                svc = [s for s in items if s["category"] == "taxi"][0]
                app = webserver.make_app()
                app["bot"] = object()
                async with TestClient(TestServer(app)) as c:
                    from datetime import datetime, timedelta
                    from config import config
                    d = (datetime.now(config.tz).date() + timedelta(days=1)).isoformat()
                    r = await c.post("/request", data={
                        "name": "Анна", "phone": "8 900 777-88-99", "pet": "Дымок, кот", "comment": "", "consent": "on",
                        "site": "", "service": "taxi", "service_id": str(svc["id"]), "address": "Москва, Ленина 1",
                        "dest": "москва,  ленина 1", "when_date": d, "when_time": "10:00", "nonce": "same-1"})
                    self.assertEqual(r.status, 400)
                    self.assertIn("совпада", await r.text())
                    n = (await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests"))["n"]
                    self.assertEqual(n, 0)
            finally:
                orders.notify_admins = orig
                await db.close_shared()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
