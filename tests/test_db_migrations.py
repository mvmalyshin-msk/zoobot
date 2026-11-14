import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import aiosqlite  # noqa: E402
import db  # noqa: E402


class DatabaseMigrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "test.db"
        self.schema_file = Path(self.directory.name) / "schema.sql"
        self.original_db_path = db.config.db_path
        object.__setattr__(db.config, "db_path", str(self.db_path))
        self.addCleanup(
            object.__setattr__, db.config, "db_path", self.original_db_path
        )

    async def _current_schema(self):
        conn = await aiosqlite.connect(self.db_path)
        conn.row_factory = aiosqlite.Row
        try:
            version = await db.current_version(conn)
            cur = await conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
            tables = {row["name"] for row in await cur.fetchall()}
            cur = await conn.execute("PRAGMA table_info(users)")
            user_columns = {row["name"] for row in await cur.fetchall()}
            return version, tables, user_columns
        finally:
            await conn.close()

    async def test_failed_initial_schema_is_rolled_back_and_can_be_retried(self):
        self.schema_file.write_text(
            """
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            CREATE TABLE broken (
            """,
            encoding="utf-8",
        )

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db, "MIGRATIONS", {}
        ):
            with self.assertRaises(aiosqlite.OperationalError):
                await db.init_db()

            version, tables, _ = await self._current_schema()
            self.assertEqual(version, 0)
            self.assertNotIn("users", tables)
            self.assertNotIn("schema_version", tables)

            self.schema_file.write_text(
                """
                CREATE TABLE schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                CREATE TABLE users (id INTEGER PRIMARY KEY);
                """,
                encoding="utf-8",
            )
            await db.init_db()

        version, tables, _ = await self._current_schema()
        self.assertEqual(version, 1)
        self.assertIn("users", tables)

    async def test_failed_migration_rolls_back_ddl_and_version_then_can_retry(self):
        self.schema_file.write_text(
            """
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            """,
            encoding="utf-8",
        )
        failing_migration = """
            ALTER TABLE users ADD COLUMN added_by_migration TEXT;
            CREATE TABLE partial_migration (id INTEGER PRIMARY KEY);
            ALTER TABLE users ADD COLUMN id INTEGER;
        """

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db, "MIGRATIONS", {2: failing_migration}
        ):
            with self.assertRaises(aiosqlite.OperationalError):
                await db.init_db()

        version, tables, user_columns = await self._current_schema()
        self.assertEqual(version, 1)
        self.assertNotIn("added_by_migration", user_columns)
        self.assertNotIn("partial_migration", tables)

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db,
            "MIGRATIONS",
            {
                2: """
                ALTER TABLE users ADD COLUMN added_by_migration TEXT;
                CREATE TABLE completed_migration (id INTEGER PRIMARY KEY);
                """
            },
        ):
            await db.init_db()

        version, tables, user_columns = await self._current_schema()
        self.assertEqual(version, 2)
        self.assertIn("added_by_migration", user_columns)
        self.assertIn("completed_migration", tables)

    async def test_failed_version_record_rolls_back_migration_ddl(self):
        self.schema_file.write_text(
            """
            CREATE TABLE schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE users (id INTEGER PRIMARY KEY);
            """,
            encoding="utf-8",
        )
        migration = "ALTER TABLE users ADD COLUMN added_by_migration TEXT;"

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db, "MIGRATIONS", {}
        ):
            await db.init_db()

        conn = await aiosqlite.connect(self.db_path)
        try:
            await conn.execute(
                """
                CREATE TRIGGER reject_version_two
                BEFORE INSERT ON schema_version
                WHEN NEW.version = 2
                BEGIN
                    SELECT RAISE(ABORT, 'version record rejected');
                END
                """
            )
            await conn.commit()
        finally:
            await conn.close()

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db, "MIGRATIONS", {2: migration}
        ):
            with self.assertRaises(aiosqlite.IntegrityError):
                await db.init_db()

        version, _, user_columns = await self._current_schema()
        self.assertEqual(version, 1)
        self.assertNotIn("added_by_migration", user_columns)

        conn = await aiosqlite.connect(self.db_path)
        try:
            await conn.execute("DROP TRIGGER reject_version_two")
            await conn.commit()
        finally:
            await conn.close()

        with mock.patch.object(db, "SCHEMA_FILE", self.schema_file), mock.patch.object(
            db, "MIGRATIONS", {2: migration}
        ):
            await db.init_db()

        version, _, user_columns = await self._current_schema()
        self.assertEqual(version, 2)
        self.assertIn("added_by_migration", user_columns)
