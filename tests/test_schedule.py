"""Единые правила времени заказа: выгул 06:00–22:00, администраторы 09:00–20:00, на подбор 2 часа, «Экспресс» снимает только 2 часа."""
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_schedule_test.db"

import timerules as schedule  # noqa: E402
from config import config  # noqa: E402


def at(h, m=0, day=17):
    return datetime(2026, 11, day, h, m, tzinfo=config.tz)


class Earliest(unittest.TestCase):
    def test_daytime_is_now_plus_two_hours_rounded_up_to_quarter(self):
        self.assertEqual(schedule.earliest(at(14, 44)), at(16, 45))
        self.assertEqual(schedule.earliest(at(10, 0)), at(12, 0))

    def test_night_waits_for_admins_then_two_hours(self):
        self.assertEqual(schedule.earliest(at(3, 10)), at(11, 0))             # пример 1: ночью → 11:00
        self.assertEqual(schedule.earliest(at(22, 30)), at(11, 0, day=18))
        self.assertEqual(schedule.earliest(at(20, 0)), at(11, 0, day=18))     # в 20:00 админы уже не работают

    def test_express_removes_only_the_two_hours(self):
        self.assertEqual(schedule.earliest(at(14, 44), express=True), at(15, 0))
        self.assertEqual(schedule.earliest(at(3, 10), express=True), at(11, 0))   # ночью экспресса нет

    def test_late_afternoon_stays_inside_service_hours(self):
        self.assertEqual(schedule.earliest(at(19, 59)), at(22, 0))


class AdminWindow(unittest.TestCase):
    def test_admin_open(self):
        self.assertTrue(schedule.admin_open(at(9, 0)))
        self.assertTrue(schedule.admin_open(at(19, 59)))
        self.assertFalse(schedule.admin_open(at(8, 59)))
        self.assertFalse(schedule.admin_open(at(20, 0)))

    def test_express_only_when_admins_work(self):
        self.assertTrue(schedule.express_available(at(12)))
        self.assertFalse(schedule.express_available(at(21)))


class Verdicts(unittest.TestCase):
    def test_example1_night_order_for_6am(self):
        v = schedule.check(at(6, 0, day=17), at(1, 30), "walk")
        self.assertFalse(v.ok)
        self.assertEqual(v.code, "admin_closed")
        self.assertIn("Нам нужно 2 часа для подбора лучших предложений", v.message)
        self.assertIn("с 09:00 утра", v.message)
        self.assertIn("11:00", v.message)

    def test_example2_thirty_minutes_without_express(self):
        now = at(14, 44)
        v = schedule.check(now + timedelta(minutes=30), now, "walk")
        self.assertEqual(v.code, "too_soon")
        self.assertIn("минимум 2 часа", v.message)
        self.assertIn("16:45", v.message)
        self.assertIn("«Экспресс»", v.message)

    def test_example3_outside_service_hours(self):
        v = schedule.check(at(23, 0, day=18), at(12), "walk")
        self.assertEqual(v.code, "out_of_service")
        self.assertIn("Выгул собак доступен с 06:00 до 22:00", v.message)

    def test_express_makes_a_close_slot_valid(self):
        now = at(14, 44)
        self.assertTrue(schedule.check(at(15, 15), now, "walk", express=True).ok)
        self.assertFalse(schedule.check(at(15, 15), now, "walk").ok)

    def test_express_at_night_is_flagged_and_ignored(self):
        v = schedule.check(at(12, 0, day=18), at(23, 0), "walk", express=True)
        self.assertTrue(v.ok)
        self.assertTrue(v.express_blocked)

    def test_express_still_needs_a_few_minutes(self):
        now = at(14, 44)
        v = schedule.check(now + timedelta(minutes=5), now, "walk", express=True)
        self.assertEqual(v.code, "too_soon")
        self.assertNotIn("поставьте галочку", v.message)

    def test_past_and_far(self):
        self.assertEqual(schedule.check(at(9), at(14), "walk").code, "past")
        self.assertEqual(schedule.check(at(12, day=17) + timedelta(days=400), at(14), "walk").code, "far")

    def test_taxi_has_the_same_rules(self):
        now = at(14, 44)
        self.assertFalse(schedule.check(now + timedelta(minutes=40), now, "taxi").ok)
        self.assertTrue(schedule.check(at(17, 0), now, "taxi").ok)

    def test_boundaries_of_service_hours(self):
        self.assertTrue(schedule.check(at(6, 0, day=18), at(12), "walk").ok)
        self.assertTrue(schedule.check(at(22, 0, day=18), at(12), "walk").ok)
        self.assertFalse(schedule.check(at(22, 15, day=18), at(12), "walk").ok)
        self.assertFalse(schedule.check(at(5, 45, day=18), at(12), "walk").ok)

    def test_slots_of_a_day_mark_each_time(self):
        now = at(14, 44)
        slots = schedule.slots(now.date(), now, "walk")
        d = {s["t"]: s["ok"] for s in slots}
        self.assertFalse(d["16:30"])
        self.assertTrue(d["16:45"] if "16:45" in d else d["17:00"])
        self.assertEqual(slots[0]["t"], "06:00")
        self.assertEqual(slots[-1]["t"], "22:00")


if __name__ == "__main__":
    unittest.main()
