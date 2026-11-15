"""Под найденными адресами в боте и в чате всегда есть «🗺 Указать точку на карте»: геопозиция в России бывает неточной,
и клиент может прислать точку или сдвинуть её на карте. Адреса вымышленные."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_addr_point_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import ReplyKeyboardMarkup  # noqa: E402

import keyboards as kb  # noqa: E402
from handlers import client as h  # noqa: E402
from handlers.client import Addr  # noqa: E402


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class PointButton(unittest.TestCase):
    def test_every_address_list_offers_the_map(self):
        for markup in (kb.address_options(["Ленина улица, 5"], False), kb.address_options([], True),
                       kb.address_options(["Ленина улица, 5"], False, confirm=True)):
            self.assertIn("ad:map", datas(markup))

    def test_map_button_sits_before_retype(self):
        d = datas(kb.address_options(["Ленина улица, 5"], False))
        self.assertLess(d.index("ad:map"), d.index("ad:retry"))

    def test_tap_asks_for_point_and_keeps_the_step(self):
        async def go():
            state = FSMContext(MemoryStorage(), StorageKey(bot_id=1, chat_id=7771, user_id=7771))
            await state.set_state(Addr.pick)
            await state.update_data(addr_target="profile", addr_back="Profile:address", addr_options=[])
            cb = mock.MagicMock(data="ad:map")
            cb.answer = mock.AsyncMock()
            cb.message = mock.MagicMock()
            cb.message.answer = mock.AsyncMock()
            await h.cb_addr(cb, state)
            return await state.get_state(), cb.message.answer.await_args
        st, call = asyncio.run(go())
        self.assertEqual(st, Addr.pick.state)
        markup = call.kwargs["reply_markup"]
        self.assertIsInstance(markup, ReplyKeyboardMarkup)
        self.assertTrue(any(getattr(b, "request_location", False) for row in markup.keyboard for b in row))
        self.assertIn("точк", call.args[0])


if __name__ == "__main__":
    unittest.main()
