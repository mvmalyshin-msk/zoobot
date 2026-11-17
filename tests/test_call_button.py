"""Кнопка «Позвонить» в админке: набирается номер клиента, и он же написан на кнопке — видно, куда звоним."""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_call_button_test.db")
os.environ["ADMIN_PHONE"] = "+74950000000"

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import portal  # noqa: E402
import utils  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(c):
    return LOOP.run_until_complete(c)


class Format(unittest.TestCase):
    def test_fmt_phone(self):
        self.assertEqual(utils.fmt_phone("+79778164660"), "+7 (977) 816-46-60")
        self.assertEqual(utils.fmt_phone("8 977 816 46 60"), "+7 (977) 816-46-60")

    def test_fmt_phone_junk_is_none(self):
        for bad in ("", None, "123", "+7 111 111 11 11"):
            self.assertIsNone(utils.fmt_phone(bad))

    def test_call_link_shows_what_it_dials(self):
        html = portal.call_link("+79778164660", "Анна")
        self.assertIn('href="tel:+79778164660"', html)
        self.assertIn("+7 (977) 816-46-60", html)
        self.assertIn("Анна", html)

    def test_dialed_number_is_normalized_not_raw(self):
        html = portal.call_link("8 977 816-46-60", "Анна")
        self.assertIn('href="tel:+79778164660"', html)

    def test_call_link_rejects_bad_numbers(self):
        self.assertEqual(portal.call_link("", "Анна"), "")
        self.assertEqual(portal.call_link("<script>", "Анна"), "")
        self.assertNotIn("tel:", portal.call_link("123", "Анна"))


class Pages(unittest.TestCase):
    def test_request_page_calls_the_clients_number_only(self):
        async def go():
            await db.init_db()
            sid = (await db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
            rid = await db.execute("INSERT INTO web_requests (service_id, name, phone, address, when_text, web_uid) VALUES (?,?,?,?,?,?)",
                                   (sid, "Максим", "+78778164660", "Мира 1", "сегодня", 910_000_777))
            app = webserver.make_app()
            app["bot"] = None
            try:
                async with TestClient(TestServer(app)) as c:
                    c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
                    return await (await c.get(f"/portal/request/{rid}")).text()
            finally:
                await db.execute("DELETE FROM web_requests WHERE id=?", (rid,))
        html = run(go())
        tels = re.findall(r'href="tel:([^"]+)"', html)
        self.assertEqual(tels, ["+78778164660"])
        self.assertIn("+7 (877) 816-46-60", html)
        self.assertNotIn("4950000000", html)  # номер администратора/сервиса не подмешивается


if __name__ == "__main__":
    unittest.main()
