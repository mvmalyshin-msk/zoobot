"""Заказы принимаем только на время с 06:00 до 22:00 (решение владельца): и кнопками, и текстом."""
import asyncio
import os
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_work_hours_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import datepicker  # noqa: E402
import db  # noqa: E402
import texts  # noqa: E402
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
TZ = config.tz
NOW = datetime(2026, 11, 8, 1, 23, tzinfo=TZ)  # как на скриншоте владельца: ночь, выбор на 09.11
DAY = date(2026, 11, 10)


def _texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


class CalendarHoursTest(unittest.TestCase):
    def test_only_hours_from_6_to_22_are_offered(self):
        hours = [t for t in _texts(datepicker.hours_markup(DAY, NOW, timedelta(days=1))) if t[:2].isdigit()]
        self.assertEqual(hours, [f"{h:02d}:00" for h in range(6, 23)])

    def test_22_has_only_22_00_and_21_has_all_quarters(self):
        self.assertEqual([t for t in _texts(datepicker.minutes_markup(DAY, 22, NOW)) if t[:2].isdigit()], ["22:00"])
        self.assertEqual([t for t in _texts(datepicker.minutes_markup(DAY, 21, NOW)) if t[:2].isdigit()],
                         ["21:00", "21:15", "21:30", "21:45"])

    def test_today_after_22_is_not_selectable(self):
        late = datetime(2026, 11, 8, 22, 10, tzinfo=TZ)
        self.assertFalse(datepicker.allowed(datetime(2026, 11, 8, 23, 0, tzinfo=TZ), late, timedelta(minutes=30)))
        cells = _texts(datepicker.month_markup(2026, 11, late, timedelta(minutes=30)))
        self.assertNotIn("8", cells)  # сегодня уже нет времени в рабочие часы

    def test_earliest_allowed_moves_into_working_hours(self):
        self.assertEqual(datepicker.first_allowed(datetime(2026, 11, 9, 1, 30, tzinfo=TZ)),
                         datetime(2026, 11, 9, 6, 0, tzinfo=TZ))
        self.assertEqual(datepicker.first_allowed(datetime(2026, 11, 9, 22, 10, tzinfo=TZ)),
                         datetime(2026, 11, 10, 6, 0, tzinfo=TZ))
        self.assertEqual(datepicker.first_allowed(datetime(2026, 11, 9, 14, 20, tzinfo=TZ)),
                         datetime(2026, 11, 9, 14, 20, tzinfo=TZ))


class TypedTimeTest(unittest.TestCase):
    def _accept(self, dt):
        from handlers import client as h
        async def go():
            await db.init_db()
            svc = await db.fetch_one("SELECT id FROM services WHERE active=1 AND unit='час' ORDER BY id LIMIT 1")
            state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=7, user_id=7))
            await state.update_data(service_id=svc["id"])
            msg = mock.MagicMock()
            msg.answer = mock.AsyncMock()
            with mock.patch.object(h, "now_utc", return_value=NOW):
                ok = await h._accept_when(msg, state, mock.MagicMock(id=7), dt)
            return ok, msg.answer.await_args.args[0]
        return LOOP.run_until_complete(go())

    def test_night_times_are_refused_with_the_working_hours(self):
        for t in ((23, 30), (5, 59), (22, 15), (0, 0)):
            ok, answer = self._accept(datetime(2026, 11, 10, *t, tzinfo=TZ))
            self.assertFalse(ok, t)
            self.assertEqual(answer, texts.OUT_OF_HOURS, t)

    def test_working_hours_edges_are_accepted(self):
        for t in ((6, 0), (22, 0), (14, 30)):
            ok, answer = self._accept(datetime(2026, 11, 10, *t, tzinfo=TZ))
            self.assertTrue(ok, t)
            self.assertNotEqual(answer, texts.OUT_OF_HOURS)

    def test_prompts_mention_working_hours(self):
        for t in (texts.ASK_WHEN, texts.ASK_WHEN_START, texts.ASK_WHEN_TAXI, texts.OUT_OF_HOURS):
            self.assertIn("06:00", t)
            self.assertIn("22:00", t)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
