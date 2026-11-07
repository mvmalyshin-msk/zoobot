"""Очевидно выдуманный номер не принимается (скриншот владельца: +7 777 777-77-77 прошёл)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

from utils import normalize_phone  # noqa: E402


class PhoneSanityTest(unittest.TestCase):
    def test_all_same_digits_or_a_run_is_not_a_phone(self):
        for fake in ("+7 777 777-77-77", "8 000 000-00-00", "+7 111 111 11 11", "1234567890", "+7 987 654-32-10",
                     "+7 012 345-67-89"):
            self.assertIsNone(normalize_phone(fake), fake)

    def test_real_numbers_still_pass(self):
        self.assertEqual(normalize_phone("+7 900 123-45-67"), "+79001234567")
        self.assertEqual(normalize_phone("8 (910) 048-66-32"), "+79100486632")
        self.assertEqual(normalize_phone("+79800000000"), "+79800000000")


if __name__ == "__main__":
    unittest.main()
