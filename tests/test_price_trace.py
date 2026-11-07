"""Откуда цена: админ видит расчёт, история заказа хранит его, а каждый расчёт пишется в лог."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_price_trace_test.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

import asyncio  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import pricing  # noqa: E402

LOOP = asyncio.new_event_loop()
BREAKDOWN = [["Передержка в зоогостинице: 14 сут. × 1800 ₽", 25200], ["Срочно (сегодня)", 200]]


class PriceExplainTest(unittest.TestCase):
    def test_staff_card_shows_the_breakdown_not_only_the_sum(self):
        text = orders.price_explanation(BREAKDOWN, 25400)
        self.assertIn("Расчёт цены", text)
        self.assertIn("14 сут. × 1800", text)
        self.assertIn("Срочно (сегодня)", text)
        self.assertIn("Итого", text)

    def test_history_note_keeps_the_formula(self):
        note = orders.price_note(BREAKDOWN, 25400)
        self.assertIn("14 сут. × 1800", note)
        self.assertIn("25", note)


class PriceLogTest(unittest.TestCase):
    def test_every_calculation_is_logged_with_its_inputs(self):
        async def go():
            await db.init_db()
            return await pricing.calculate_price(6, {"quantity": 14, "start_at": None, "pet_size": None})
        with self.assertLogs("pricing", level="INFO") as cm:
            total, _ = LOOP.run_until_complete(go())
        line = " ".join(cm.output)
        self.assertIn("услуга=6", line)
        self.assertIn("кол-во=14", line)
        self.assertIn(f"итог={total}", line)


class SameStatusTest(unittest.TestCase):
    def test_same_status_is_explained_not_reported_as_a_broken_transition(self):
        from unittest import mock
        fake = {"id": 6, "status": "cancelled", "client_phone": "+7"}
        async def go():
            with mock.patch.object(orders, "get_order_full", mock.AsyncMock(return_value=fake)):
                return await orders.change_status(None, 6, "cancelled", "admin")
        ok, msg = LOOP.run_until_complete(go())
        self.assertFalse(ok)
        self.assertIn("уже в статусе", msg)
        self.assertNotIn("Нельзя перевести", msg)


class HistoryColumnTest(unittest.TestCase):
    def test_status_log_has_note_column(self):
        async def go():
            await db.init_db()
            return await db.fetch_all("PRAGMA table_info(order_statuses_log)")
        cols = {c["name"] for c in LOOP.run_until_complete(go())}
        self.assertIn("note", cols)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
