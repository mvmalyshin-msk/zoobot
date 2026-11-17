"""Экспресс-заявки видны сразу везде бургундским цветом: список и карточка в админке, кабинет клиента, страница «принято»."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_express_view_test.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import cabinet  # noqa: E402
import db  # noqa: E402
import portal  # noqa: E402
import webchat  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
BURGUNDY = "#7a1f3d"


def row(express, status="new"):
    return {"kind": "form", "id": 77, "status": status, "service": "Выгул", "category": "walk", "source": "form", "name": "Анна",
            "phone": "+79001112233", "address": "Мира 1", "when_text": "17.11.2026, 12:30 · ⚡ Экспресс",
            "created_at": "2026-11-17 09:00:00", "express": express, "admin_note": None}


class ExpressView(unittest.TestCase):
    def test_portal_list_row_is_burgundy_only_for_express(self):
        x, plain = portal._row_html(row(1)), portal._row_html(row(0))
        self.assertIn("express", x.split(">")[0])
        self.assertIn("ЭКСПРЕСС", x)
        self.assertNotIn("express", plain.split(">")[0])
        self.assertNotIn("ЭКСПРЕСС", plain)
        self.assertIn(BURGUNDY, portal.CSS)

    def test_done_page_marks_express(self):
        self.assertIn("Экспресс", webform.render_done(5, express=True))
        self.assertNotIn("Экспресс", webform.render_done(5))

    def test_cabinet_list_marks_express(self):
        async def go():
            await db.init_db()
            uid = 910_000_001
            sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
            await db.execute("INSERT INTO web_requests (service_id, name, phone, address, web_uid, express) VALUES (?,?,?,?,?,1)",
                             (sid, "Анна", "+79001112233", "Мира 1", uid))
            await db.execute("INSERT INTO web_requests (service_id, name, phone, address, web_uid, express) VALUES (?,?,?,?,?,0)",
                             (sid, "Анна", "+79001112233", "Мира 2", uid))
            try:
                return await cabinet._requests_html(uid, uid)
            finally:
                await db.execute("DELETE FROM web_requests WHERE web_uid=?", (uid,))
        html = LOOP.run_until_complete(go())
        self.assertEqual(html.count("Экспресс"), 1)
        self.assertIn(BURGUNDY, html)

    def test_admin_request_card_is_burgundy(self):
        async def go():
            await db.init_db()
            sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
            rid = await db.execute("INSERT INTO web_requests (service_id, name, phone, address, express) VALUES (?,?,?,?,1)",
                                   (sid, "Анна", "+79001112233", "Мира 1"))
            app = webserver.make_app()
            app["bot"] = None
            try:
                async with TestClient(TestServer(app)) as c:
                    c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
                    return await (await c.get(f"/portal/request/{rid}")).text()
            finally:
                await db.execute("DELETE FROM web_requests WHERE id=?", (rid,))
        html = LOOP.run_until_complete(go())
        self.assertIn("ЭКСПРЕСС", html)
        self.assertIn('class="reg express"', html)


if __name__ == "__main__":
    unittest.main()
