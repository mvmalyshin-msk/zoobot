"""Ввод адреса и вида питомца: «квартира 46» — не адрес; «кошка» словами — понятно."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import breeds  # noqa: E402
import geo  # noqa: E402


class FlatOnly(unittest.TestCase):
    def test_flat_phrases(self):
        for text, flat in (("квартира 46", "46"), ("кв 46", "46"), ("Кв. 12А", "12А"), ("оф 5", "5"),
                           ("кв. №7", "7"), ("46", "46"), ("  квартира   46 ", "46")):
            self.assertEqual(geo.flat_only(text), flat, text)

    def test_real_addresses_are_not_flats(self):
        for text in ("Ленина 5", "ул. Ленина, 5, кв. 12", "Химки, Железнодорожная 33", "квартира", "кв", "5 квартал",
                     "1234567", "первомайский квартал"):
            self.assertIsNone(geo.flat_only(text), text)


class Species(unittest.TestCase):
    def test_words(self):
        for text, sp in (("кошка", "кошка"), ("Кот", "кошка"), ("котик", "кошка"), ("котёнок", "кошка"),
                         ("собака", "собака"), ("Пёс", "собака"), ("щенок", "собака"), ("песик", "собака"),
                         ("другое", "другое"), ("Другой", "другое")):
            self.assertEqual(breeds.parse_species(text), sp, text)

    def test_unknown(self):
        for text in ("хомяк", "ывфв", "", "дракон", "кошка собака"):
            self.assertIsNone(breeds.parse_species(text), text)


if __name__ == "__main__":
    unittest.main()
