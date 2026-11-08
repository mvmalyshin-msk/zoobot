"""После удаления данных (и отмены) нижняя панель с кнопками карты/геопозиции убирается, а не остаётся висеть."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_erase_kb_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402
from aiogram.types import ReplyKeyboardRemove  # noqa: E402

import db  # noqa: E402
import texts  # noqa: E402
from handlers import client as h  # noqa: E402

LOOP = asyncio.new_event_loop()


async def _press(data, uid=9301, fresh=True):
    await db.init_db()
    if fresh:
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (uid,))
        await db.execute("UPDATE users SET name='М', phone='+79100486632', address='Химки', erased_at=NULL WHERE tg_id=?", (uid,))
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    cb = mock.MagicMock()
    cb.data = data
    cb.answer = mock.AsyncMock()
    cb.message.answer = mock.AsyncMock()
    cb.from_user = mock.MagicMock(id=uid, username=None)
    await h.cb_erase(cb, state)
    return cb.message.answer


class EraseKeyboardTest(unittest.TestCase):
    def test_done_removes_reply_keyboard(self):
        ans = LOOP.run_until_complete(_press("del:yes", 9301))
        ans.assert_awaited_once()
        self.assertEqual(ans.call_args.args[0], texts.ERASE_DONE)
        self.assertIsInstance(ans.call_args.kwargs.get("reply_markup"), ReplyKeyboardRemove)

    def test_cancel_removes_reply_keyboard(self):
        ans = LOOP.run_until_complete(_press("del:no", 9302))
        self.assertIsInstance(ans.call_args.kwargs.get("reply_markup"), ReplyKeyboardRemove)

    def test_second_press_does_not_repeat_done(self):
        async def go():
            first = await _press("del:yes", 9303)
            second = await _press_again()
            return first, second
        async def _press_again():
            state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=9303, user_id=9303))
            cb = mock.MagicMock()
            cb.data = "del:yes"
            cb.answer = mock.AsyncMock()
            cb.message.answer = mock.AsyncMock()
            cb.from_user = mock.MagicMock(id=9303, username=None)
            await h.cb_erase(cb, state)
            return cb.message.answer
        first, second = LOOP.run_until_complete(go())
        second.assert_not_awaited()      # данные уже удалены: повторное «Готово» не нужно


if __name__ == "__main__":
    unittest.main()
