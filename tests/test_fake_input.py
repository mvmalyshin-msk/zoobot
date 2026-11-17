"""Оскорбления в тексте и выдуманные номера в заявке не принимаются (сервер и браузер одинаково)."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

os.environ["DB_PATH"] = "/tmp/zoobot_fake_input_test.db"
import profanity  # noqa: E402
from utils import looks_fake_phone  # noqa: E402


class Insults(unittest.TestCase):
    def test_insults_and_rude_words_are_refused(self):
        for t in ("Жопа", "ты идиот", "дурак", "тупица", "урод", "мразь", "Сволочи", "тварь", "дебил"):
            self.assertTrue(profanity.has_profanity(t), t)

    def test_pet_talk_is_not_touched(self):
        for t in ("Дурашка", "Лохматый пёс", "сука лабрадора, 3 года", "Уходова улица", "Бодрый кобель", "тупой нож"):
            self.assertFalse(profanity.has_profanity(t), t)


class FakePhones(unittest.TestCase):
    def test_made_up_numbers_are_refused(self):
        for n in ("+79871234567", "+79001234567", "+79991111111", "+79267654321", "+70001234455", "+79121212121"):
            self.assertTrue(looks_fake_phone(n), n)

    def test_normal_numbers_pass(self):
        for n in ("+79264173582", "+79162045831", "+79031234455", "+74951234455", "+79991112233"):
            self.assertFalse(looks_fake_phone(n), n)


if __name__ == "__main__":
    unittest.main()
