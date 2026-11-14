import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import db  # noqa: E402


class DatabaseJournalModeTest(unittest.IsolatedAsyncioTestCase):
    async def test_wal_is_configured_at_init_not_on_each_connection(self):
        statements = []
        original_path = db.config.db_path
        original_connect = db.aiosqlite.connect

        with tempfile.TemporaryDirectory() as directory:
            object.__setattr__(db.config, "db_path", str(Path(directory) / "zoobot.db"))

            async def traced_connect(*args, **kwargs):
                conn = await original_connect(*args, **kwargs)
                await conn.set_trace_callback(statements.append)
                return conn

            try:
                with mock.patch.object(db.aiosqlite, "connect", new=traced_connect):
                    await db.init_db()
                    self.assertEqual(await db.fetch_one("SELECT 1 AS value"), {"value": 1})
                    self.assertEqual(await db.fetch_all("SELECT 2 AS value"), [{"value": 2}])
            finally:
                await db.close_shared()      # общее соединение открыто подменённым connect — закрыть, иначе держит процесс
                object.__setattr__(db.config, "db_path", original_path)

        journal_mode_statements = [
            sql.strip().upper() for sql in statements
            if sql.strip().upper().startswith("PRAGMA JOURNAL_MODE")
        ]
        self.assertEqual(journal_mode_statements, ["PRAGMA JOURNAL_MODE = WAL"])
