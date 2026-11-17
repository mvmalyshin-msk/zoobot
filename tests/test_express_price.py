"""«Экспресс» стоит дороже: надбавка считается в цене заказа, оформленного из экспресс-заявки; её можно менять в тарифах."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_express_price_test.db")

import db  # noqa: E402
import pricing  # noqa: E402
import tariffs_web  # noqa: E402

LOOP = asyncio.new_event_loop()


async def price(express):
    await db.init_db()
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    return await pricing.calculate_price(sid, {"quantity": 1, "is_urgent": False, "is_express": express, "outside_mkad": False,
                                               "pet_size": "M", "pet_traits": None, "pet_notes": "", "pet_species": "собака",
                                               "start_at": None, "route": None})


class ExpressPrice(unittest.TestCase):
    def test_express_costs_more_and_is_listed_in_breakdown(self):
        plain, _ = LOOP.run_until_complete(price(False))
        fast, breakdown = LOOP.run_until_complete(price(True))
        self.assertGreater(fast, plain)
        self.assertTrue(any("Экспресс" in str(x[0]) for x in breakdown), breakdown)

    def test_condition_is_known_and_titled(self):
        self.assertIn("express", pricing.CONDITIONS)
        self.assertIn("express", tariffs_web.CONDITION_TITLES)


if __name__ == "__main__":
    unittest.main()
