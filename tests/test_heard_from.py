"""Вопрос «Как вы узнали о нас?»: колонка в базе, кнопки, запись ответа только один раз."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_heard_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import keyboards as kb  # noqa: E402


class HeardFromTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()

    async def test_keyboard_covers_all_sources(self):
        rows = kb.heard_from().inline_keyboard
        data = {b.callback_data for r in rows for b in r}
        self.assertEqual(data, {f"src:{k}" for k in kb.HEARD_FROM})
        for d in data:
            self.assertLessEqual(len(d), 64)

    async def test_answer_saved_once(self):
        await db.execute("INSERT INTO users (tg_id) VALUES (?)", (555,))
        u = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (555,))
        self.assertIsNone(u["heard_from"])
        sql = "UPDATE users SET heard_from=? WHERE id=? AND heard_from IS NULL"
        await db.execute(sql, ("vk", u["id"]))
        await db.execute(sql, ("chat", u["id"]))
        u = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (555,))
        self.assertEqual(u["heard_from"], "vk")


if __name__ == "__main__":
    unittest.main()
