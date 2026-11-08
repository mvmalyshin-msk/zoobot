"""Методика цен (ноябрь 2026): база = рынок (Dogsy, Яндекс Услуги, «Наша няня»), клиент платит базу + наценка сервиса 30 %;
в каталоге «от» — уже с наценкой."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_market_prices_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import keyboards as kb  # noqa: E402
import pricing  # noqa: E402

BASE = {"walk30": 700, "walk60": 1000, "sitter_visit": 800, "sitter_live": 3000,
        "boarding_home": 1500, "boarding_hotel": 1600, "taxi": 189}
FROM = {"walk30": 910, "walk60": 1300, "sitter_visit": 1040, "sitter_live": 3900,
        "boarding_home": 1950, "boarding_hotel": 2080, "taxi": 246}


def run(coro):
    return asyncio.run(coro)


async def _setup():
    await db.init_db()


class Prices(unittest.TestCase):
    def test_base_prices(self):
        async def t():
            await _setup()
            rows = await db.fetch_all("SELECT code, base_price FROM services")
            return {r["code"]: r["base_price"] for r in rows}
        self.assertEqual(run(t()), BASE)

    def test_surcharges(self):
        async def t():
            await _setup()
            big = await db.fetch_one("SELECT effect_type, value FROM price_rules WHERE condition_type='pet_size_l'")
            urgent = await db.fetch_one("SELECT effect_type, value FROM price_rules WHERE condition_type='urgent_today'")
            return dict(big), dict(urgent)
        big, urgent = run(t())
        self.assertEqual(big, {"effect_type": "percent", "value": 20})
        self.assertEqual(urgent, {"effect_type": "fixed", "value": 300})

    def test_service_markup_is_30(self):
        async def t():
            await _setup()
            return await pricing.markup_percent()
        self.assertEqual(run(t()), 30)

    def test_catalog_from_price_includes_markup(self):
        async def t():
            await _setup()
            items = await db.fetch_all("SELECT * FROM services WHERE active=1 ORDER BY id")
            return {s["code"]: s["from_price"] for s in await pricing.with_client_price(items)}
        self.assertEqual(run(t()), FROM)

    def test_admin_price_is_not_overwritten_by_migration(self):
        async def t():
            await _setup()
            await db.execute("UPDATE services SET base_price=777 WHERE code='walk30'")
            await db.init_db()
            got = (await db.fetch_one("SELECT base_price FROM services WHERE code='walk30'"))["base_price"]
            await db.execute("UPDATE services SET base_price=700 WHERE code='walk30'")  # вернуть для соседних тестов
            return got
        self.assertEqual(run(t()), 777)


class Keyboards(unittest.TestCase):
    ITEMS = [{"id": 1, "code": "walk30", "category": "walk", "name": "Выгул 30 минут", "base_price": 700, "from_price": 910}]

    def test_category_button_uses_from_price(self):
        m = kb.service_categories(self.ITEMS)
        self.assertTrue(any("от 910" in b.text for r in m.inline_keyboard for b in r))

    def test_variant_button_uses_from_price(self):
        m = kb.category_variants(self.ITEMS, "walk")
        self.assertTrue(any("от 910" in b.text for r in m.inline_keyboard for b in r))


if __name__ == "__main__":
    unittest.main()
