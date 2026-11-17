"""Мобильная админка: карточка заявки и «Оформить заказ» — порядок действий, предзаполненное время, крупные зоны нажатия."""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_portal_mobile_test.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import portal  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()


async def card(when_text):
    await db.init_db()
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    rid = await db.execute("INSERT INTO web_requests (service_id, name, phone, address, when_text, web_uid) VALUES (?,?,?,?,?,?)",
                           (sid, "Анна", "+79001112233", "Мира 1", when_text, 910_000_002))
    app = webserver.make_app()
    app["bot"] = None
    try:
        async with TestClient(TestServer(app)) as c:
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
            return await (await c.get(f"/portal/request/{rid}")).text()
    finally:
        await db.execute("DELETE FROM web_requests WHERE id=?", (rid,))


class PortalMobile(unittest.TestCase):
    def test_order_start_is_prefilled_from_request_time(self):
        html = LOOP.run_until_complete(card("17.11.2026, 12:30 · ⚡ Экспресс"))
        self.assertIn('name="start" type="datetime-local" required value="2026-11-17T12:30"', html)

    def test_start_stays_empty_when_time_unknown(self):
        html = LOOP.run_until_complete(card("с 17.11.2026 по 20.11.2026"))
        self.assertIn('name="start" type="datetime-local" required value=""', html)

    def test_actions_order_contact_status_order_cancel(self):
        html = LOOP.run_until_complete(card("17.11.2026, 12:30"))
        pos = [html.index(m) for m in ("💬 Чат с клиентом", 'id="status"', "Оформить заказ</h2>", "Отменить заявку")]
        self.assertEqual(pos, sorted(pos))

    def test_mobile_css_has_big_tap_targets_and_stacked_forms(self):
        css = portal.CSS
        self.assertIn("@media(max-width:640px)", css)
        self.assertIn("min-height:48px", css)
        self.assertIn("font-size:16px", css)
        self.assertIn(".actions{display:grid;grid-template-columns:1fr 1fr", css)

    def test_make_order_qty_and_button_are_not_on_one_line(self):
        html = LOOP.run_until_complete(card("17.11.2026, 12:30"))
        form = html[html.index("Оформить заказ</h2>"):]
        form = form[:form.index("</form>")]
        self.assertIn('class="field"', form)
        self.assertEqual(len(re.findall(r'<div class="field">', form)), 2)


if __name__ == "__main__":
    unittest.main()
