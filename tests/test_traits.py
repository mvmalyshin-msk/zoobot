"""Повадки собаки (анкета для выгульщика): коды, подписи, предупреждения."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import traits  # noqa: E402


class Traits(unittest.TestCase):
    def test_codes_are_short_and_unique(self):
        codes = [c for c, _ in traits.TRAITS]
        self.assertEqual(len(codes), len(set(codes)))
        for c in codes:
            self.assertLessEqual(len(f"tr:{c}".encode()), 64)

    def test_toggle(self):
        self.assertEqual(traits.toggle([], "pull"), ["pull"])
        self.assertEqual(traits.toggle(["pull"], "pull"), [])
        self.assertEqual(traits.toggle(["pull"], "pick"), ["pull", "pick"])

    def test_toggle_ignores_unknown_code(self):
        self.assertEqual(traits.toggle(["pull"], "hack"), ["pull"])

    def test_pack_unpack_roundtrip_and_garbage(self):
        self.assertEqual(traits.unpack(traits.pack(["pick", "pull", "bogus"])), ["pull", "pick"])  # порядок как в списке
        self.assertEqual(traits.unpack(None), [])
        self.assertEqual(traits.unpack(""), [])
        self.assertEqual(traits.unpack("zzz,pull"), ["pull"])

    def test_describe(self):
        self.assertEqual(traits.describe(["pull", "paws"]), "тянет поводок; протирать лапы после прогулки")
        self.assertEqual(traits.describe([]), "")

    def test_danger_flag(self):
        self.assertTrue(traits.has_danger(["aggr_dog"]))
        self.assertTrue(traits.has_danger(["aggr_people", "pull"]))
        self.assertFalse(traits.has_danger(["pull", "pick"]))


class Card(unittest.TestCase):
    def order(self, **kw):
        o = {"id": 7, "status": "assigned", "service_name": "Выгул 60 минут", "start_at": "2026-11-12 09:00:00",
             "end_at": None, "unit": "выгул", "address_to": None, "address": "ул. Ленина, 1", "outside_mkad": 0,
             "address_unverified": 0, "pet_name": "Бим", "pet_species": "собака", "pet_size": "M",
             "pet_breed": None, "pet_notes": "Хромает", "pet_traits": "pull,aggr_dog", "client_name": "Анна",
             "client_phone": "+79001234567", "price_total": 800}
        o.update(kw)
        return o

    def test_staff_card_shows_traits_with_warning(self):
        import orders
        t = orders.order_card(self.order(), for_staff=True)
        self.assertIn("Повадки: тянет поводок; агрессия к собакам", t)
        self.assertIn("⚠️", t)

    def test_client_card_has_no_traits(self):
        import orders
        self.assertNotIn("Повадки", orders.order_card(self.order()))

    def test_no_traits_no_line(self):
        import orders
        self.assertNotIn("Повадки", orders.order_card(self.order(pet_traits=None), for_staff=True))


if __name__ == "__main__":
    unittest.main()
