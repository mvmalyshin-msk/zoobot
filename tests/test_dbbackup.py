import os, sqlite3, tempfile, unittest
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ.setdefault("DB_PATH", os.path.join(_tmp, "t.db"))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import dbbackup


class Backup(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.src = self.d / "src.db"
        c = sqlite3.connect(self.src)
        c.execute("CREATE TABLE t(x)"); c.execute("INSERT INTO t VALUES (42)"); c.commit(); c.close()

    async def test_copy_is_readable_and_has_data(self):
        out = await dbbackup.run_once(src=self.src, dest_dir=self.d / "b", stamp="2026-01-01")
        self.assertTrue(out.exists())
        self.assertEqual(sqlite3.connect(out).execute("SELECT x FROM t").fetchone()[0], 42)

    async def test_keeps_only_last_n(self):
        for i in range(1, 11):
            await dbbackup.run_once(src=self.src, dest_dir=self.d / "b", stamp=f"2026-01-{i:02d}", keep=7)
        names = sorted(p.name for p in (self.d / "b").glob("auto-*.db"))
        self.assertEqual(len(names), 7)
        self.assertEqual(names[0], "auto-2026-01-04.db")

    async def test_missing_source_does_not_raise(self):
        self.assertIsNone(await dbbackup.run_once(src=self.d / "nope.db", dest_dir=self.d / "b", stamp="x"))


if __name__ == "__main__":
    unittest.main()
