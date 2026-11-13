"""Проверка полной резервной копии SQLite для миграции."""
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:test-token")
os.environ.setdefault("ADMIN_IDS", "111")

from portal import sqlite_backup_bytes  # noqa: E402


class SqliteBackupTest(unittest.TestCase):
    def test_backup_contains_committed_wal_data_and_passes_integrity_check(self):
        with tempfile.TemporaryDirectory(prefix="zoobot-backup-test-") as folder:
            source_path = str(Path(folder) / "live.sqlite3")
            live = sqlite3.connect(source_path)
            try:
                live.execute("PRAGMA journal_mode=WAL")
                live.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                live.execute("INSERT INTO sample(value) VALUES (?)", ("сохранённая запись",))
                live.commit()

                snapshot = sqlite_backup_bytes(source_path)
                self.assertTrue(snapshot.startswith(b"SQLite format 3\x00"))
                restored = sqlite3.connect(":memory:")
                try:
                    restored.deserialize(snapshot)
                    self.assertEqual(restored.execute("SELECT value FROM sample").fetchone()[0], "сохранённая запись")
                    self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                finally:
                    restored.close()
            finally:
                live.close()

    def test_missing_database_is_not_replaced_with_an_empty_file(self):
        with tempfile.TemporaryDirectory(prefix="zoobot-backup-test-") as folder:
            missing = str(Path(folder) / "does-not-exist.sqlite3")
            with self.assertRaises(FileNotFoundError):
                sqlite_backup_bytes(missing)


if __name__ == "__main__":
    unittest.main()
