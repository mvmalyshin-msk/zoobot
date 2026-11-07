import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_releases_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import orders  # noqa: E402
import releases  # noqa: E402


class ReleasesTest(unittest.TestCase):
    def test_numbers_descend_and_have_items(self):
        nums = [r["number"] for r in releases.RELEASES]
        self.assertEqual(nums, sorted(nums, reverse=True))
        self.assertEqual(len(set(nums)), len(nums))
        for r in releases.RELEASES:
            self.assertTrue(r["items"] and r["title"])

    def test_render_has_number_and_items(self):
        t = releases.render(releases.RELEASES[0])
        self.assertIn(f"обновление №{releases.CURRENT}", t)
        self.assertEqual(t.count("•"), len(releases.RELEASES[0]["items"]))
        self.assertLess(len(t), 4000)

    def test_announce_once_after_delivery(self):
        sent = []

        async def fake_notify(bot, tg_id, text, markup=None):
            sent.append((tg_id, text))
            return True

        async def run():
            await db.init_db()
            orig = orders.notify
            orders.notify = fake_notify
            try:
                self.assertTrue(await releases.announce(None, [111]))
                self.assertFalse(await releases.announce(None, [111]))  # повторно не шлём
            finally:
                orders.notify = orig
        asyncio.run(run())
        self.assertEqual(len(sent), len(releases.RELEASES))
        self.assertIn(f"обновление №{releases.RELEASES[-1]['number']}", sent[0][1])  # старые первыми
        self.assertIn(f"обновление №{releases.CURRENT}", sent[-1][1])

    def test_not_marked_if_nobody_received(self):
        async def fake_notify(bot, tg_id, text, markup=None):
            return False

        async def run():
            await db.execute("DELETE FROM kv")
            orig = orders.notify
            orders.notify = fake_notify
            try:
                self.assertFalse(await releases.announce(None, [111]))
            finally:
                orders.notify = orig
            return await db.fetch_one("SELECT value FROM kv WHERE key='last_release'")
        self.assertIsNone(asyncio.run(run()))


if __name__ == "__main__":
    unittest.main()
