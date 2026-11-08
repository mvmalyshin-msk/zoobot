"""Собственные заходы владельца (телефон и ноут, его клиентская учётка) в аналитику не попадают."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_analytics_owner_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import analytics  # noqa: E402
import db  # noqa: E402
import webchat  # noqa: E402
from tests.test_analytics import post, rows, run  # noqa: E402


async def _client_user(uid: int):
    await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (?, 'Клиент', '+79100486632')", (uid,))


class OwnerExcluded(unittest.TestCase):
    def test_admin_page_visit_marks_device_so_later_visits_are_not_counted(self):
        async def t(c):
            r = await c.get("/portal")                       # владелец в портале: устройство помечается
            self.assertIn(analytics.OWNER_COOKIE, r.cookies)
            c.session.cookie_jar.clear_domain(c.host)        # админская cookie исчезла (вышел), метка устройства осталась
            c.session.cookie_jar.update_cookies({analytics.OWNER_COOKIE: "1"})
            await post(c, [{"t": "pv", "p": "/"}])
            self.assertEqual(await rows(), [])
        run(t, admin=True)

    def test_owner_client_profile_on_same_browser_is_ignored_everywhere(self):
        async def t(c):
            await _client_user(777)
            c.session.cookie_jar.update_cookies({webchat.COOKIE: await webchat.new_session(777)})
            await c.get("/portal")                           # браузер с админской и клиентской учёткой разом
            self.assertTrue(await analytics.is_ignored(777))
            self.assertFalse(await analytics.is_ignored(778))
            await analytics.bot_event(777, "cmd", "/start")
            self.assertEqual(await rows("source='bot'"), [])
        run(t, admin=True)

    def test_client_cookie_of_ignored_user_is_not_counted_but_others_are(self):
        async def t(c):
            await _client_user(779)
            await analytics.ignore_user(779)
            c.session.cookie_jar.update_cookies({webchat.COOKIE: await webchat.new_session(779)})
            await post(c, [{"t": "pv", "p": "/"}])
            self.assertEqual(await rows(), [])
        run(t)
        async def t2(c):
            await _client_user(780)
            c.session.cookie_jar.update_cookies({webchat.COOKIE: await webchat.new_session(780)})
            await post(c, [{"t": "pv", "p": "/"}])
            self.assertEqual(len(await rows()), 1)
        run(t2)

    def test_ignoring_a_user_removes_their_past_bot_events(self):
        async def t(c):
            await analytics.bot_event(888, "cmd", "/start")
            self.assertEqual(len(await rows("source='bot'")), 1)
            await analytics.ignore_user(888)
            self.assertEqual(await rows("source='bot'"), [])
        run(t)


if __name__ == "__main__":
    unittest.main()
