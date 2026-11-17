"""«Экспресс» в Telegram-боте: кнопка при «слишком рано» только в часы админов, запас 15 минут, цена выше, обработчик включает режим."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_bot_express_test.db")

import handlers.client as client_h  # noqa: E402
from config import config  # noqa: E402


def clock(h, m=0):
    now = datetime(2026, 11, 18, h, m, tzinfo=config.tz)
    return mock.patch.object(client_h, "now_utc", return_value=now.astimezone(timezone.utc))


class BotExpress(unittest.TestCase):
    def test_express_shortens_the_wait_in_working_hours(self):
        with clock(14):
            self.assertEqual(client_h.min_ahead({"category": "walk"}), timedelta(hours=2))
            self.assertEqual(client_h.min_ahead({"category": "walk"}, True), timedelta(minutes=15))

    def test_express_does_not_help_at_night(self):
        with clock(3, 10):
            self.assertEqual(client_h.min_ahead({"category": "walk"}, True), client_h.min_ahead({"category": "walk"}))

    def test_button_only_while_admins_work(self):
        with clock(12):
            self.assertEqual(client_h.express_markup().inline_keyboard[0][0].callback_data, "xp:on")
        for h in (3, 8, 20, 23):
            with clock(h):
                self.assertIsNone(client_h.express_markup(), h)

    def test_handler_exists_and_turns_express_on(self):
        cb, state = mock.AsyncMock(), mock.AsyncMock()
        state.get_data.return_value = {"service_id": 1}
        svc = {"category": "walk", "unit": "час"}
        with clock(12), mock.patch.object(client_h, "_service_of", return_value=svc), \
                mock.patch.object(client_h, "ask_when", new=mock.AsyncMock()) as ask:
            asyncio.run(client_h.cb_express(cb, state))
        state.update_data.assert_awaited_with(express=True)
        self.assertTrue(ask.await_args.kwargs["express"])

    def test_handler_refuses_at_night(self):
        cb, state = mock.AsyncMock(), mock.AsyncMock()
        with clock(23):
            asyncio.run(client_h.cb_express(cb, state))
        state.update_data.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
