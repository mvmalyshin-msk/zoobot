"""Проверки адреса, клички, породы, размера. Без сети: геокодер подменён готовыми ответами."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import breeds as b  # noqa: E402
import geo  # noqa: E402

KHIMKI = {"lat": "55.9", "lon": "37.3", "display_name": "x", "address": {
    "road": "Железнодорожная улица", "house_number": "33", "suburb": "Сходня", "city": "Химки",
    "state": "Московская область", "country_code": "ru"}}
MSK = {"lat": "55.75", "lon": "37.6", "display_name": "x", "address": {
    "road": "Тверская улица", "house_number": "7", "suburb": "Тверской район", "city": "Москва",
    "state": "Москва", "country_code": "ru"}}
ZEL = {"lat": "55.98", "lon": "37.19", "display_name": "x", "address": {
    "road": "Панфиловский проспект", "house_number": "10", "city_district": "Зеленоград", "city": "Москва",
    "state": "Москва", "country_code": "ru"}}


class PetName(unittest.TestCase):
    def test_good(self):
        for n in ("Рекс", "мишка", "Мишка-Пуша", "Грэй", "Лаки"):
            self.assertIsNotNone(b.check_pet_name(n), n)

    def test_capitalised(self):
        self.assertEqual(b.check_pet_name("мишка"), "Мишка")

    def test_garbage(self):
        for n in ("ывфвф", "asdf", "12", "Ж", "аааа", "ъъъ", "!!!", "тест", "а" * 40):
            self.assertIsNone(b.check_pet_name(n), n)

    def test_obscene(self):
        for n in ("говно", "Говнюк", "хуй", "пизда", "сука", "Ебан", "мудак"):
            self.assertIsNone(b.check_pet_name(n), n)

    def test_obscene_lookalikes_pass(self):
        for n in ("Мандарин", "Скипидар", "Хуан", "Херувим", "Сукин"[:0] or "Сэм"):
            self.assertIsNotNone(b.check_pet_name(n), n)


class PersonName(unittest.TestCase):
    def test_bad(self):
        for n in ("ааа", "х", "Qwerty", "говно", "123"):
            self.assertIsNone(b.check_person_name(n), n)

    def test_good(self):
        self.assertEqual(b.check_person_name("анна"), "Анна")


class Breed(unittest.TestCase):
    def test_known(self):
        v = b.check_breed("собака", "Лабрадор")
        self.assertEqual((v.kind, v.size), ("ok", "L"))

    def test_typo_gets_suggestion(self):
        v = b.check_breed("собака", "лабродор")
        self.assertEqual(v.kind, "suggest")
        self.assertTrue(any("Лабрадор" in o for o in v.options))

    def test_mixed(self):
        self.assertEqual(b.check_breed("собака", "метис").kind, "mixed")
        self.assertEqual(b.check_breed("кошка", "-").kind, "mixed")

    def test_junk(self):
        for t in ("ывфвф", "asdf", "123", "говно", "большой"):
            self.assertIn(b.check_breed("собака", t).kind, ("junk", "unknown"), t)
        self.assertEqual(b.check_breed("собака", "ывфвф").kind, "junk")
        self.assertEqual(b.check_breed("собака", "говно").kind, "junk")

    def test_wrong_species(self):
        v = b.check_breed("кошка", "лабрадор")
        self.assertEqual((v.kind, v.other_species), ("wrong_species", "собака"))

    def test_cat(self):
        self.assertEqual(b.check_breed("кошка", "мейн-кун").kind, "ok")
        self.assertEqual(b.check_breed("кошка", "британец").kind, "ok")

    def test_real_but_unlisted_is_unknown_not_junk(self):
        self.assertEqual(b.check_breed("собака", "Тосса-ину").kind, "unknown")


class OtherAnimal(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(b.check_other_animal("хомяк").kind, "ok")
        self.assertEqual(b.check_other_animal("Попугай").kind, "ok")

    def test_typo(self):
        self.assertEqual(b.check_other_animal("хомяг").kind, "suggest")

    def test_junk_and_fantasy(self):
        self.assertEqual(b.check_other_animal("ывфв").kind, "junk")
        self.assertEqual(b.check_other_animal("говно").kind, "junk")
        self.assertEqual(b.check_other_animal("дракон").kind, "unknown")

    def test_dog_or_cat_redirect(self):
        self.assertEqual(b.check_other_animal("собака").kind, "wrong_species")


class Size(unittest.TestCase):
    def test_words(self):
        self.assertEqual(b.parse_size("большой"), "L")
        self.assertEqual(b.parse_size("Крупная"), "L")
        self.assertEqual(b.parse_size("средний"), "M")
        self.assertEqual(b.parse_size("маленький"), "S")
        self.assertEqual(b.parse_size("мелкий"), "S")

    def test_kg(self):
        self.assertEqual(b.parse_size("10 кг"), "S")
        self.assertEqual(b.parse_size("10,5 кг"), "M")
        self.assertEqual(b.parse_size("25кг"), "M")
        self.assertEqual(b.parse_size("30 килограмм"), "L")
        self.assertEqual(b.parse_size("3"), "S")

    def test_letters(self):
        self.assertEqual(b.parse_size("l"), "L")
        self.assertEqual(b.parse_size("М"), "M")  # русская М

    def test_none(self):
        for t in ("ывфв", "привет", "0 кг", "900 кг", ""):
            self.assertIsNone(b.parse_size(t), t)


class AddressText(unittest.TestCase):
    def test_accept(self):
        for t in ("железнодорожная 33 1 238", "ул. Ленина, 1, кв. 5", "Москва, Шереметьево, терминал B"):
            self.assertTrue(geo.looks_like_address(t), t)

    def test_reject(self):
        for t in ("ывфвф 12", "123", "говно", "Химки", "ааааа 5", "", "ул"):
            self.assertFalse(geo.looks_like_address(t), t)


class Parse(unittest.TestCase):
    def test_suburb_and_outside(self):
        p = geo.parse_nominatim([KHIMKI])[0]
        self.assertIn("Железнодорожная", p.label)
        self.assertIn("33", p.label)
        self.assertEqual(p.district, "Сходня")
        self.assertTrue(p.outside_mkad)
        self.assertAlmostEqual(p.lat, 55.9)

    def test_moscow_inside(self):
        p = geo.parse_nominatim([MSK])[0]
        self.assertFalse(p.outside_mkad)
        self.assertEqual(p.district, "Тверской район")

    def test_zelenograd_outside(self):
        self.assertTrue(geo.parse_nominatim([ZEL])[0].outside_mkad)

    def test_not_russia_dropped(self):
        far = dict(MSK, address=dict(MSK["address"], country_code="de"))
        self.assertEqual(geo.parse_nominatim([far]), [])

    def test_outside_region_dropped(self):
        spb = dict(MSK, lat="59.9", lon="30.3", address=dict(MSK["address"], state="Санкт-Петербург"))
        self.assertEqual(geo.parse_nominatim([spb]), [])

    def test_dedupe_and_limit(self):
        self.assertEqual(len(geo.parse_nominatim([KHIMKI] * 3 + [MSK] * 4)), 2)

    def test_no_house_number_ok_but_flagged(self):
        a = dict(KHIMKI["address"]); a.pop("house_number")
        p = geo.parse_nominatim([dict(KHIMKI, address=a)])[0]
        self.assertFalse(p.has_house)

    def test_split_flat(self):
        self.assertEqual(geo.split_flat("Ленина 1, кв. 5"), ("Ленина 1", "5"))
        self.assertEqual(geo.split_flat("Ленина 1 квартира 12"), ("Ленина 1", "12"))
        self.assertEqual(geo.split_flat("Ленина 1"), ("Ленина 1", None))

    def test_query_variants(self):
        v = geo.query_variants("железнодорожная 33 1 238")
        self.assertEqual(v[0], "железнодорожная 33 1 238")
        self.assertIn("железнодорожная 33", v)
        self.assertLessEqual(len(v), 3)


if __name__ == "__main__":
    unittest.main()
