"""Выбор даты и времени кнопками: календарь, часы, минуты (без сети и без Telegram)."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import datepicker as dp  # noqa: E402
from config import config  # noqa: E402

NOW = datetime(2026, 11, 7, 15, 46, tzinfo=config.tz)  # суббота
DAY = timedelta(days=1)


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def datas(markup):
    return [b.callback_data for b in buttons(markup)]


class Month(unittest.TestCase):
    def test_title_and_weekdays(self):
        m = dp.month_markup(2026, 11, NOW)
        rows = m.inline_keyboard
        self.assertIn("Ноябрь 2026", rows[0][1].text)
        self.assertEqual([b.text for b in rows[1]], ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"])

    def test_past_days_are_dead_buttons(self):
        m = dp.month_markup(2026, 11, NOW)
        d = datas(m)
        self.assertNotIn("cal:d:2026-11-06", d)  # вчера
        self.assertIn("cal:d:2026-11-07", d)  # сегодня (время ещё выберем)
        self.assertIn("cal:d:2026-11-30", d)

    def test_past_week_is_all_dead(self):
        # 1 ноября 2026 — воскресенье, первая неделя целиком в прошлом (сегодня 7-е)
        first_week = dp.month_markup(2026, 11, NOW).inline_keyboard[2]
        self.assertEqual(len(first_week), 7)
        self.assertEqual({b.callback_data for b in first_week}, {"cal:x"})

    def test_december_starts_on_tuesday(self):
        first_week = dp.month_markup(2026, 12, NOW).inline_keyboard[2]
        self.assertEqual(first_week[0].callback_data, "cal:x")  # пустая клетка в понедельник
        self.assertEqual(first_week[1].callback_data, "cal:d:2026-12-01")

    def test_no_navigation_before_current_month(self):
        d = datas(dp.month_markup(2026, 11, NOW))
        self.assertNotIn("cal:m:2026-10", d)
        self.assertIn("cal:m:2026-12", d)

    def test_year_wrap(self):
        d = datas(dp.month_markup(2026, 12, NOW))
        self.assertIn("cal:m:2026-11", d)
        self.assertIn("cal:m:2027-01", d)

    def test_no_navigation_past_a_year(self):
        d = datas(dp.month_markup(2027, 11, NOW))
        self.assertIn("cal:m:2027-10", d)
        self.assertNotIn("cal:m:2027-12", d)
        self.assertNotIn("cal:d:2027-11-09", d)  # дальше 366 дней от 07.11.2026

    def test_every_row_has_seven_cells(self):
        for row in dp.month_markup(2027, 2, NOW).inline_keyboard[2:]:
            self.assertEqual(len(row), 7)

    def test_callback_data_fits_telegram_limit(self):
        for m in (dp.month_markup(2026, 11, NOW), dp.hours_markup(date(2026, 11, 8), NOW),
                  dp.minutes_markup(date(2026, 11, 8), 23, NOW)):
            for b in buttons(m):
                self.assertLessEqual(len(b.callback_data.encode()), 64)


class Buttons(unittest.TestCase):
    def test_no_blank_button_text(self):
        for m in (dp.month_markup(2026, 11, NOW), dp.month_markup(2026, 12, NOW, DAY)):
            for b in buttons(m):
                self.assertTrue(b.text.strip(), repr(b.text))


class Hours(unittest.TestCase):
    def test_future_day_has_all_24_hours(self):
        d = [x for x in datas(dp.hours_markup(date(2026, 11, 8), NOW)) if x.startswith("cal:h:")]
        self.assertEqual(len(d), 24)
        self.assertIn("cal:h:2026-11-08:00", d)
        self.assertIn("cal:h:2026-11-08:23", d)

    def test_today_hides_hours_that_are_too_soon(self):
        # сейчас 15:46, нужен запас 30 минут → самый ранний слот 16:16, значит час 16 ещё можно (16:30)
        d = datas(dp.hours_markup(date(2026, 11, 7), NOW))
        self.assertNotIn("cal:h:2026-11-07:15", d)
        self.assertIn("cal:h:2026-11-07:16", d)
        self.assertNotIn("cal:h:2026-11-07:00", d)

    def test_has_back_to_calendar(self):
        self.assertIn("cal:m:2026-11", datas(dp.hours_markup(date(2026, 11, 8), NOW)))

    def test_late_evening_today_has_no_hours(self):
        late = datetime(2026, 11, 7, 23, 50, tzinfo=config.tz)
        hours = [x for x in datas(dp.hours_markup(date(2026, 11, 7), late)) if x.startswith("cal:h:")]
        self.assertEqual(hours, [])


class Minutes(unittest.TestCase):
    def test_four_quarters(self):
        d = [x for x in datas(dp.minutes_markup(date(2026, 11, 8), 10, NOW)) if x.startswith("cal:t:")]
        self.assertEqual(d, ["cal:t:2026-11-08:10:00", "cal:t:2026-11-08:10:15",
                             "cal:t:2026-11-08:10:30", "cal:t:2026-11-08:10:45"])

    def test_today_current_hour_hides_too_soon(self):
        # 16:00 и 16:15 раньше 16:16 → остаются 16:30 и 16:45
        d = [x for x in datas(dp.minutes_markup(date(2026, 11, 7), 16, NOW)) if x.startswith("cal:t:")]
        self.assertEqual(d, ["cal:t:2026-11-07:16:30", "cal:t:2026-11-07:16:45"])

    def test_back_to_hours(self):
        self.assertIn("cal:d:2026-11-08", datas(dp.minutes_markup(date(2026, 11, 8), 10, NOW)))


class Parse(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(dp.parse("cal:m:2026-12"), ("m", date(2026, 12, 1), None))
        self.assertEqual(dp.parse("cal:d:2026-11-08"), ("d", date(2026, 11, 8), None))
        self.assertEqual(dp.parse("cal:h:2026-11-08:09"), ("h", date(2026, 11, 8), 9))
        self.assertEqual(dp.parse("cal:t:2026-11-08:09:45"),
                         ("t", datetime(2026, 11, 8, 9, 45, tzinfo=config.tz), None))
        self.assertEqual(dp.parse("cal:x"), ("x", None, None))

    def test_garbage_is_rejected(self):
        for bad in ("cal:", "cal:d:2026-13-40", "cal:h:2026-11-08:99", "cal:t:2026-11-08:10:07",
                    "cal:d:abc", "cal:m:2026-00", "cal:z:1", "cal:t:2026-11-08:24:00", ""):
            self.assertIsNone(dp.parse(bad), bad)



class LeadTime(unittest.TestCase):
    """Все услуги, кроме такси: записать можно не раньше чем через сутки."""

    def test_today_is_dead_when_a_day_ahead_is_required(self):
        d = datas(dp.month_markup(2026, 11, NOW, DAY))
        self.assertNotIn("cal:d:2026-11-07", d)
        self.assertIn("cal:d:2026-11-08", d)  # завтра есть слоты после 15:46

    def test_hours_tomorrow_start_after_the_same_time(self):
        # сейчас 15:46 → самый ранний слот завтра 15:46, значит 16:00 и позже
        h = datas(dp.hours_markup(date(2026, 11, 8), NOW, DAY))
        self.assertNotIn("cal:h:2026-11-08:15", h)
        self.assertIn("cal:h:2026-11-08:16", h)
        self.assertNotIn("cal:h:2026-11-08:00", h)
        self.assertIn("cal:h:2026-11-09:00", datas(dp.hours_markup(date(2026, 11, 9), NOW, DAY)))

    def test_minutes_tomorrow(self):
        late = datetime(2026, 11, 7, 15, 20, tzinfo=config.tz)
        m = datas(dp.minutes_markup(date(2026, 11, 8), 15, late, DAY))
        self.assertNotIn("cal:t:2026-11-08:15:15", m)  # 15:15 раньше 15:20
        self.assertIn("cal:t:2026-11-08:15:30", m)

    def test_default_keeps_short_lead_for_taxi(self):
        self.assertIn("cal:d:2026-11-07", datas(dp.month_markup(2026, 11, NOW)))


if __name__ == "__main__":
    unittest.main()
