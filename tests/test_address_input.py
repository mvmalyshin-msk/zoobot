"""Ввод адреса и вида питомца: «квартира 46» — не адрес; «кошка» словами — понятно."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

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


TROITSK_CHEL = {"lat": "54.09", "lon": "61.57", "name": "Ленина", "address": {
    "road": "улица Ленина", "house_number": "1", "town": "Троицк", "state": "Челябинская область",
    "country": "Россия", "country_code": "ru"}}
TROITSK_MSK = {"lat": "55.48", "lon": "37.30", "name": "Троицк", "address": {
    "city": "Троицк", "state": "Москва", "country": "Россия", "country_code": "ru"}}
MINSK = {"lat": "53.9", "lon": "27.56", "name": "Ленина", "address": {
    "road": "улица Ленина", "city": "Минск", "country": "Беларусь", "country_code": "by"}}


class Elsewhere(unittest.TestCase):
    """Адрес нашёлся, но не в Москве и области: скажем, где именно."""

    def test_other_region(self):
        self.assertEqual(geo.parse_elsewhere([TROITSK_CHEL]), "Троицк, Челябинская область")

    def test_other_country(self):
        self.assertEqual(geo.parse_elsewhere([MINSK]), "Минск, Беларусь")

    def test_inside_region_is_not_elsewhere(self):
        self.assertIsNone(geo.parse_elsewhere([TROITSK_MSK]))
        self.assertIsNone(geo.parse_elsewhere([]))

    def test_skips_inside_and_takes_first_outside(self):
        self.assertEqual(geo.parse_elsewhere([TROITSK_MSK, TROITSK_CHEL]), "Троицк, Челябинская область")

    def test_garbage_items(self):
        self.assertIsNone(geo.parse_elsewhere([{}, {"lat": "x"}, None]))


class FirstWords(unittest.TestCase):
    def test_town_candidates(self):
        self.assertEqual(geo.town_guess("троицк ленина 1"), "троицк")
        self.assertEqual(geo.town_guess("г. Химки, Ленина 5"), "химки")
        self.assertEqual(geo.town_guess("Ленина 5"), None)  # одно слово до цифры — это улица, не город
        self.assertEqual(geo.town_guess("5"), None)


class Explain(unittest.TestCase):
    def test_outside_message_names_place_and_area(self):
        import texts
        t = texts.addr_not_found(geo.Diagnosis(where="Троицк, Челябинская область", town=None), 1)
        self.assertIn("Троицк, Челябинская область", t)
        self.assertIn("Москве и Московской области", t)

    def test_outside_plus_town_in_region_asks_to_clarify(self):
        import texts
        t = texts.addr_not_found(geo.Diagnosis(where="Троицк, Челябинская область", town="Троицк"), 1)
        self.assertIn("Челябинская область", t)
        self.assertIn("Троицк", t)
        self.assertIn("улиц", t)

    def test_town_only(self):
        import texts
        t = texts.addr_not_found(geo.Diagnosis(where=None, town="Троицк"), 1)
        self.assertIn("Троицк", t)
        self.assertIn("улиц", t)
        self.assertNotIn("Москве и Московской области", t)

    def test_nothing_known_falls_back(self):
        import texts
        self.assertEqual(texts.addr_not_found(geo.Diagnosis(None, None), 1), texts.ADDR_NOT_FOUND)
        self.assertEqual(texts.addr_not_found(None, 2), texts.ADDR_NOT_FOUND_KEEP)

    def test_outside_never_offers_keep_as_written(self):
        import texts
        t = texts.addr_not_found(geo.Diagnosis(where="Минск, Беларусь", town=None), 5)
        self.assertNotIn("оставить как написано", t)


if __name__ == "__main__":
    unittest.main()
