"""Диапазон цены на экране заказа: от расчёта до +15 % (округляем вверх до 10 ₽)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import pricing  # noqa: E402


class PriceRange(unittest.TestCase):
    def test_low_is_the_calculation(self):
        self.assertEqual(pricing.price_range(1040)[0], 1040)

    def test_high_is_plus_15_percent_rounded_up_to_ten(self):
        self.assertEqual(pricing.price_range(1040), (1040, 1200))  # 1196 → 1200
        self.assertEqual(pricing.price_range(1000), (1000, 1150))  # ровно 1150
        self.assertEqual(pricing.price_range(1235), (1235, 1430))  # 1420,25 → 1430

    def test_high_is_always_above_low(self):
        for total in (1, 9, 10, 99, 100, 250, 19999):
            low, high = pricing.price_range(total)
            self.assertGreater(high, low, total)
            self.assertEqual(high % 10, 0)

    def test_format_has_both_bounds_and_no_total_line(self):
        t = pricing.format_range(1040)
        self.assertIn("1 040 ₽", t)
        self.assertIn("1 200 ₽", t)
        self.assertIn("от", t)
        self.assertNotIn("Итого", t)

    def test_breakdown_without_total(self):
        t = pricing.format_breakdown_lines([("Выгул 60 мин", 700), ("Крупная собака", 100)])
        self.assertIn("• Выгул 60 мин: 700 ₽", t)
        self.assertIn("• Крупная собака: +100 ₽", t)
        self.assertNotIn("Итого", t)


if __name__ == "__main__":
    unittest.main()
