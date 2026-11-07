"""Синяя лента — клиентские экраны, красная — администратор. Смешивать нельзя: на каждом экране одна лента."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_ribbons_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from aiogram.types import User  # noqa: E402

import db  # noqa: E402
import ribbons  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
BLUE, RED = "#1d6fd6", "#d62b2b"


def _ribbons(text: str) -> list[str]:
    return [s for s in text.split("role-ribbon")[1:]]


class RibbonModuleTest(unittest.TestCase):
    def test_client_ribbon_is_blue_and_admin_ribbon_is_red(self):
        self.assertIn(BLUE, ribbons.html(ribbons.CLIENT))
        self.assertIn("КЛИЕНТСКОЕ МЕНЮ", ribbons.html(ribbons.CLIENT))
        self.assertIn(RED, ribbons.html(ribbons.ADMIN))
        self.assertIn("АДМИНИСТРАТОР", ribbons.html(ribbons.ADMIN))
        self.assertNotIn(RED, ribbons.html(ribbons.CLIENT))
        self.assertNotIn(BLUE, ribbons.html(ribbons.ADMIN))

    def test_telegram_prefix_marks_the_role_in_first_line(self):
        self.assertTrue(ribbons.tg_prefix(ribbons.CLIENT).startswith("🔵 КЛИЕНТСКОЕ МЕНЮ"))
        self.assertTrue(ribbons.tg_prefix(ribbons.ADMIN).startswith("🔴 АДМИНИСТРАТОР"))


class PortalRibbonTest(unittest.TestCase):
    def test_portal_pages_show_only_the_admin_ribbon(self):
        import portal
        out = portal.page("Заявки", "<p>x</p>")
        self.assertEqual(len(_ribbons(out)), 1)
        self.assertIn(RED, out)
        self.assertNotIn(BLUE, out)

    def test_portal_login_shows_admin_ribbon(self):
        import portal
        out = portal.login_page()
        self.assertIn(RED, out)
        self.assertNotIn(BLUE, out)


class FormRibbonTest(unittest.TestCase):
    def test_client_request_form_is_blue(self):
        src = (Path(__file__).resolve().parent.parent / "webform.py").read_text(encoding="utf-8")
        self.assertIn("ribbons.html(ribbons.CLIENT)", src)


class ChatRibbonTest(unittest.TestCase):
    def _page(self, uid_sessions):
        async def go():
            await db.init_db()
            await db.execute("DELETE FROM web_sessions")
            webchat._linked_cache.clear()
            app = webserver.make_app()
            async with TestClient(TestServer(app)) as c:
                headers = {}
                if uid_sessions is not None:
                    token = await webchat.new_session(uid_sessions)
                    headers = {"Cookie": f"{webchat.COOKIE}={token}"}
                return await (await c.get("/chat", headers=headers)).text()
        return LOOP.run_until_complete(go())

    def test_guest_chat_is_blue(self):
        out = self._page(None)
        self.assertIn(BLUE, out)
        self.assertNotIn(RED, out)

    def test_admin_chat_is_red(self):
        out = self._page(111)
        self.assertIn(RED, out)
        self.assertNotIn(BLUE, out)

    def test_admin_chat_has_no_client_links(self):
        """Не переплетаем: клиентский «Кабинет» админу не показываем, у клиента он есть."""
        self.assertNotIn('href="/cabinet"', self._page(111))
        self.assertIn('href="/cabinet"', self._page(None))

    def test_client_cabinet_is_blue(self):
        async def go():
            await db.init_db()
            app = webserver.make_app()
            async with TestClient(TestServer(app)) as c:
                r = await c.get("/cabinet")
                return await r.text()
        out = LOOP.run_until_complete(go())
        self.assertIn(BLUE, out)
        self.assertNotIn(RED, out)


class TelegramMenuRibbonTest(unittest.TestCase):
    def test_admin_and_client_menus_start_with_their_role_mark(self):
        import handlers.client as hc
        async def go():
            await db.init_db()
            sent = {}
            for who, uid in (("client", 6201), ("admin", 111)):
                msg = mock.MagicMock()
                msg.answer = mock.AsyncMock()
                await hc.show_menu(msg, User(id=uid, is_bot=False, first_name="T"))
                sent[who] = msg.answer.await_args.args[0]
            return sent
        sent = LOOP.run_until_complete(go())
        self.assertTrue(sent["client"].startswith("🔵"), sent["client"][:40])
        self.assertTrue(sent["admin"].startswith("🔴"), sent["admin"][:40])


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
