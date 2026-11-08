"""Правки по замечаниям владельца (ноябрь 2026):
1) способ связи больше не спрашиваем нигде; 2) такси — не раньше чем через 40 минут;
3) клиенту цена — только вилка «от и до», без расчёта и наценки; 5) администратору — вилка и расчёт;
4) панель карты/геопозиции на сайте не «висит» после использования;
6–7) портал: переключатель «Заявки / Клиенты», у каждой заявки дата и время регистрации."""
import asyncio
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_ops_panel_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import datepicker  # noqa: E402
import keyboards as kb  # noqa: E402
import orders  # noqa: E402
import pricing  # noqa: E402
import texts  # noqa: E402
import webchat  # noqa: E402
import webform  # noqa: E402
from config import config  # noqa: E402
from handlers import client as client_h  # noqa: E402


def _labels(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class NoContactChoice(unittest.TestCase):
    def test_texts_and_keyboards_have_no_contact_step(self):
        self.assertFalse(hasattr(texts, "ASK_CONTACT"))
        self.assertFalse(hasattr(kb, "contact_pref"))
        self.assertFalse(hasattr(client_h, "cb_contact"))
        self.assertFalse(hasattr(client_h.Profile, "contact"))

    def test_settings_have_no_contact_button(self):
        self.assertFalse(any("связи" in t.lower() for t in _labels(kb.settings(False))))

    def test_web_form_has_no_contact_select(self):
        html = webform.render_form([], {}, {})
        self.assertNotIn("Как с вами связаться", html)
        self.assertNotIn('name="contact"', html)

    def test_web_form_validate_ignores_contact(self):
        clean, errors = webform.validate({"name": "Ира", "phone": "+79001234567", "address": "Мира 1", "consent": "on",
                                          "service_id": "1", "contact": "sms"})
        self.assertNotIn("contact", clean)


class Taxi40Minutes(unittest.TestCase):
    NOW = datetime(2026, 11, 8, 17, 1, tzinfo=config.tz)

    def test_taxi_needs_forty_minutes(self):
        self.assertEqual(client_h.min_ahead({"category": "taxi"}), timedelta(minutes=40))
        self.assertEqual(datepicker.TAXI_AHEAD, timedelta(minutes=40))

    def test_first_free_quarter_is_not_before_now_plus_40(self):
        day = date(2026, 11, 8)
        mins = [b.text for row in datepicker.minutes_markup(day, 17, self.NOW, datepicker.TAXI_AHEAD).inline_keyboard for b in row
                if b.text[:2].isdigit()]
        self.assertEqual(mins, ["17:45"])  # 17:41 → ближайшая четверть 17:45; 17:30 и 17:15 раньше нельзя

    def test_text_input_too_early_names_the_earliest_taxi_time(self):
        self.assertIn("40 минут", texts.PAST_WHEN)
        self.assertNotIn("30 минут", texts.PAST_WHEN)

    def test_other_services_still_need_a_day(self):
        self.assertEqual(client_h.min_ahead({"category": "walk"}), timedelta(days=1))


class PriceForClient(unittest.TestCase):
    BREAKDOWN = [("Зоотакси: посадка", 189), ("Перевозка животного", 150), ("Наценка сервиса (+30%)", 207)]

    def _order(self, **kw):
        o = {"id": 20, "status": "created", "service_name": "Зоотакси", "category": "taxi", "unit": "поездка",
             "start_at": "2026-11-08 14:45:00", "end_at": None, "address": "Подача", "address_to": "Куда",
             "outside_mkad": 0, "rider_name": None, "comment": None, "address_unverified": 0, "pet_name": None,
             "client_name": "Максим", "client_phone": "+79778164660", "price_total": 896,
             "price_breakdown": '[["Зоотакси: посадка", 189], ["Наценка сервиса (+30%)", 207]]',
             "created_at": "2026-11-08 14:05:12"}
        o.update(kw)
        return o

    def test_client_card_shows_only_the_range(self):
        t = orders.order_card(self._order())
        self.assertIn("от 896 ₽ до 1 040 ₽", t)
        self.assertNotIn("Сумма:", t)
        self.assertNotIn("Наценка", t)
        self.assertNotIn("Расчёт", t)

    def test_my_orders_list_shows_the_range(self):
        t = orders.format_my_orders([{"id": 20, "status": "created", "name": "Зоотакси", "start_at": "2026-11-08 14:45:00",
                                      "price_total": 896, "created_at": "2026-11-08 14:05:12"}])
        self.assertIn("от 896 ₽ до 1 040 ₽", t)

    def test_admin_card_has_the_range_the_client_was_told_and_the_calculation(self):
        t = orders.order_card(self._order(), for_staff=True)
        self.assertIn("Вилка для клиента: от 896 ₽ до 1 040 ₽", t)
        self.assertIn("Расчёт цены", t)
        self.assertIn("Наценка сервиса (+30%)", t)

    def test_admin_card_has_registration_stamp(self):
        t = orders.order_card(self._order(), for_staff=True)
        self.assertIn("Зарегистрирован: 08.11.2026 17:05", t)  # 14:05 UTC = 17:05 Москва

    def test_admin_card_has_no_contact_line(self):
        self.assertNotIn("Связь:", orders.order_card(self._order(), for_staff=True))

    def test_range_text_helper(self):
        self.assertEqual(pricing.format_range_short(896), "от 896 ₽ до 1 040 ₽")

    def test_confirm_screen_has_no_breakdown(self):
        import inspect
        src = inspect.getsource(client_h.show_confirm)
        self.assertNotIn("format_breakdown_lines", src)
        self.assertNotIn("Что учтено", src)


class CabinetPrice(unittest.TestCase):
    def test_site_cabinet_shows_range_not_total(self):
        import cabinet
        import db

        async def go():
            await db.init_db()
            return await cabinet._orders_html([{"id": 999001, "name": "Зоотакси", "pet_name": None, "start_at": "2026-11-08 14:45:00",
                                                "price_total": 896, "status": "created"}])
        html = asyncio.run(go())
        self.assertIn("от 896 ₽ до 1 040 ₽", html)
        self.assertNotIn("· 896 ₽<br>", html)


class ReplyPanelFlag(unittest.TestCase):
    def test_one_time_keyboard_is_marked_for_the_browser(self):
        out = webchat.serialize_markup(kb.address_ask())
        self.assertTrue(out.get("once"))
        self.assertTrue(out["reply"])


if __name__ == "__main__":
    unittest.main()
