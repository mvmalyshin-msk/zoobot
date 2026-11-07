"""Шаг диалога хранится в базе и переживает перезапуск бота.
Раньше он жил в памяти: после каждой выкладки адрес с карты приходил «никуда», и бот молчал (скриншот владельца)."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_fsm_store_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.storage.base import StorageKey  # noqa: E402

import db  # noqa: E402
import fsm_store  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(fn):
    async def go():
        await db.init_db()
        return await fn()
    return LOOP.run_until_complete(go())


def key(uid):
    return StorageKey(bot_id=1, chat_id=uid, user_id=uid)


class SQLiteStorageTest(unittest.TestCase):
    def test_state_and_data_survive_a_restart(self):
        when = datetime(2026, 11, 9, 9, 15, tzinfo=timezone.utc)
        async def go():
            before = fsm_store.SQLiteStorage()
            await before.set_state(key(501), "Profile:address")
            await before.set_data(key(501), {"name": "Фунтик", "phone": "+79001234567", "when": when,
                                             "addr_options": [{"label": "Химки", "has_house": True}]})
            after = fsm_store.SQLiteStorage()  # новый процесс после выкладки
            return await after.get_state(key(501)), await after.get_data(key(501))
        state, data = run(go)
        self.assertEqual(state, "Profile:address")
        self.assertEqual(data["name"], "Фунтик")
        self.assertEqual(data["when"], when)
        self.assertEqual(data["addr_options"], [{"label": "Химки", "has_house": True}])

    def test_people_do_not_share_steps_and_clear_works(self):
        async def go():
            st = fsm_store.SQLiteStorage()
            await st.set_state(key(502), "Order:when")
            await st.update_data(key(502), {"service_id": 3})
            other = await st.get_state(key(503)), await st.get_data(key(503))
            await st.set_state(key(502), None)
            await st.set_data(key(502), {})
            return other, await st.get_state(key(502)), await st.get_data(key(502))
        other, cleared_state, cleared_data = run(go)
        self.assertEqual(other, (None, {}))
        self.assertIsNone(cleared_state)
        self.assertEqual(cleared_data, {})

    def test_bot_uses_the_database_storage(self):
        from bot import build_dispatcher
        self.assertIsInstance(build_dispatcher().storage, fsm_store.SQLiteStorage)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
