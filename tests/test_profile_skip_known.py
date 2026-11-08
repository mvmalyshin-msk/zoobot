"""Бот не переспрашивает то, что уже знает: имя, телефон, адрес берутся из профиля, спрашивается только недостающее."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_skip_known_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import db  # noqa: E402
import texts  # noqa: E402
from handlers import client as h  # noqa: E402
from handlers.client import Profile  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(fn):
    async def go():
        await db.init_db()
        return await fn()
    return LOOP.run_until_complete(go())


async def _case(uid, name, phone, address):
    await db.execute("DELETE FROM users WHERE tg_id=?", (uid,))
    await db.execute("INSERT INTO users (tg_id, name, phone, address) VALUES (?,?,?,?)", (uid, name, phone, address))
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    m = mock.MagicMock()
    m.answer = mock.AsyncMock()
    tg = mock.MagicMock(id=uid, username=None, first_name="X")
    await h.start_profile(m, state, after="order", tg_user=tg)
    said = " ".join(str(c.args[0]) for c in m.answer.call_args_list if c.args)
    return await state.get_state(), said


class SkipKnownTest(unittest.TestCase):
    def test_phone_known_name_missing_asks_only_name(self):
        st, said = run(lambda: _case(9101, None, "+79100486632", None))
        self.assertEqual(st, Profile.name.state)
        self.assertIn(texts.ASK_NAME, said)

    def test_name_and_phone_known_does_not_ask_phone(self):
        st, said = run(lambda: _case(9102, "Максим", "+79100486632", None))
        self.assertEqual(st, Profile.address.state)
        self.assertNotIn(texts.ASK_NAME, said)
        self.assertNotIn("телефон", said.lower())

    def test_name_missing_phone_known_then_name_goes_to_address(self):
        async def go():
            await _case(9103, None, "+79100486632", None)
            state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=9103, user_id=9103))
            await state.update_data(phone="+79100486632", phone_verified=None)
            await state.set_state(Profile.name)
            m = mock.MagicMock()
            m.answer = mock.AsyncMock()
            m.text = "Максим"
            m.from_user = mock.MagicMock(id=9103, username=None)
            await h.p_name(m, state)
            return await state.get_state()
        self.assertEqual(run(go), Profile.address.state)

    def test_everything_known_goes_to_pet(self):
        st, said = run(lambda: _case(9104, "Максим", "+79100486632", "Химки, Ленина 5"))
        self.assertEqual(st, Profile.pet_name.state)
        self.assertNotIn(texts.ASK_NAME, said)

    def test_profile_edit_still_asks_name(self):
        async def go():
            await _case(9105, "Максим", "+79100486632", "Химки")
            state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=9105, user_id=9105))
            m = mock.MagicMock()
            m.answer = mock.AsyncMock()
            await h.start_profile(m, state, after=None)
            return await state.get_state()
        self.assertEqual(run(go), Profile.name.state)


if __name__ == "__main__":
    unittest.main()
