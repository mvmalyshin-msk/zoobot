"""Серверная проверка адреса во всех услугах: «Москва слезам не верит» не принимается ни в выгуле, ни в зоонянe, ни в общей форме.
Адреса вымышленные, карта (geo.search) подменена."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_addr_check_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import geo  # noqa: E402
import webform  # noqa: E402


def place(label, house=True):
    return geo.Place(label=label, district="Москва", lat=55.7, lon=37.6, outside_mkad=False, has_house=house)


def run(cat, text, found, key="address", ok=""):
    clean, errors = {key: text, key + "_ok": ok}, {}

    async def fake(_):
        return found
    with mock.patch.object(geo, "search", side_effect=fake):
        asyncio.run(webform.check_addresses(clean, errors, cat))
    return clean, errors


class AddressForEveryService(unittest.TestCase):
    def test_no_house_number_is_refused_for_walk_and_sitter(self):
        for cat in ("walk", "sitter"):
            _, e = run(cat, "Москва слезам не верит", [])
            self.assertIn("Укажите номер дома", e.get("address", ""), cat)

    def test_unknown_address_with_digits_is_refused(self):
        _, e = run("walk", "Абвгд 55", [])
        self.assertIn("Не нашёл такой адрес", e["address"])

    def test_street_without_such_house_is_refused(self):
        _, e = run("sitter", "Ленина улица, 99", [place("Ленина улица", house=False)])
        self.assertIn("дома с таким номером", e["address"])

    def test_exact_address_is_accepted(self):
        clean, e = run("walk", "Ленина улица, 5, Москва", [place("Ленина улица, 5, Москва")])
        self.assertEqual(e, {})
        self.assertEqual(clean["address_ok"], "Ленина улица, 5, Москва")

    def test_other_house_offers_choices(self):
        clean, e = run("walk", "Ленина 5", [place("Ленина улица, 5, Москва"), place("Ленина улица, 7, Москва")])
        self.assertIn("Выберите адрес из подсказок", e["address"])
        self.assertEqual(len(clean["address_opts"]), 2)

    def test_picked_from_list_skips_search(self):
        text = "Ленина улица, 5, Москва"
        _, e = run("walk", text, [], ok=text)
        self.assertEqual(e, {})

    def test_map_down_does_not_block_but_house_needed(self):
        _, e = run("walk", "Ленина 5", None)
        self.assertEqual(e, {})
        _, e = run("walk", "Ленина", None)
        self.assertIn("номер дома", e["address"])

    def test_boarding_accepts_district_without_house(self):
        _, e = run("boarding", "Химки", [place("Химки, Московская область", house=False)])
        self.assertEqual(e, {})

    def test_boarding_refuses_unknown_place(self):
        _, e = run("boarding", "Абвгдеж", [])
        self.assertIn("Не нашёл", e["address"])

    def test_taxi_still_checks_both_ends(self):
        clean = {"address": "Тверская улица", "address_ok": "", "dest": "Ленина 5", "dest_ok": ""}
        errors = {}

        async def fake(_):
            return None
        with mock.patch.object(geo, "search", side_effect=fake):
            asyncio.run(webform.check_addresses(clean, errors, "taxi"))
        self.assertIn("address", errors)
        self.assertNotIn("dest", errors)


if __name__ == "__main__":
    unittest.main()
