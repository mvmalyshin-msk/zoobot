"""Страница /contact: связаться с администратором без входа и меню — Telegram, звонок, «перезвоните мне» одной формой."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_contact_test.db"

import contact  # noqa: E402
import db  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402

GOOD = {"name": "Анна", "phone": "8 900 123-45-67", "message": "Нужен выгул завтра", "consent": "on"}


class ContactTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        webform.RATE.clear()
        await db.execute("DELETE FROM web_requests")
        app = webserver.make_app()
        app["bot"] = mock.MagicMock()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        await db.execute("DELETE FROM web_requests")      # общая база тестов: не оставляем заявки другим

    async def test_home_chat_button_leads_straight_to_contact(self):
        t = await (await self.client.get("/")).text()
        self.assertIn('href="/contact">Связаться с нами</a>', t)
        self.assertIn("Хотите сначала обсудить?", t)

    async def test_home_buttons_stack_evenly_on_phone(self):
        t = await (await self.client.get("/")).text()
        self.assertIn(".hero-actions{display:flex", t)
        self.assertIn("@media(max-width:640px)", t)
        self.assertIn(".hero-actions{align-items:stretch;flex-direction:column", t)

    async def test_page_has_three_ways_no_phone_link_without_number(self):
        r = await self.client.get("/contact")
        self.assertEqual(r.status, 200)
        t = await r.text()
        self.assertIn("https://t.me/zooconcierge_bot", t)
        self.assertIn('action="/contact"', t)
        self.assertIn("Перезвоните мне", t)
        self.assertNotIn("tel:", t)                                   # номер администратора не задан — кнопки «Позвонить» нет

    async def test_call_button_hides_the_number_but_still_dials(self):
        with mock.patch.object(contact, "ADMIN_PHONE", "+7 900 111-22-33"):
            t = await (await self.client.get("/contact")).text()
            self.assertIn('href="/contact/call"', t)
            for leak in ("111-22-33", "111 22", "79001112233", "tel:"):
                self.assertNotIn(leak, t)                                  # номера нет ни на экране, ни в коде страницы
            r = await self.client.get("/contact/call", allow_redirects=False)
            self.assertEqual((r.status, r.headers["Location"]), (302, "tel:+79001112233"))   # звонок идёт по сотовой сети
            self.assertIn("no-store", r.headers["Cache-Control"])

    async def test_call_link_closed_without_number(self):
        r = await self.client.get("/contact/call", allow_redirects=False)
        self.assertEqual(r.status, 404)

    async def test_valid_request_saved_and_admin_notified(self):
        with mock.patch.object(contact.orders, "notify_admins", mock.AsyncMock()) as n:
            r = await self.client.post("/contact", data=GOOD)
            self.assertEqual(r.status, 200)
            self.assertIn("принято", await r.text())
            n.assert_awaited_once()
            self.assertIn("+79001234567", n.await_args.args[1])
        row = await db.fetch_one("SELECT * FROM web_requests WHERE name='Анна' ORDER BY id DESC LIMIT 1")
        self.assertEqual((row["phone"], row["contact_pref"], row["service_id"]), ("+79001234567", "call", None))
        self.assertIn("Нужен выгул завтра", row["comment"])

    async def test_bad_phone_no_consent_and_bot_trap_refused(self):
        with mock.patch.object(contact.orders, "notify_admins", mock.AsyncMock()) as n:
            r = await self.client.post("/contact", data=dict(GOOD, phone="123"))
            self.assertEqual(r.status, 400)
            self.assertIn("Проверьте номер", await r.text())
            r = await self.client.post("/contact", data={k: v for k, v in GOOD.items() if k != "consent"})
            self.assertEqual(r.status, 400)
            await self.client.post("/contact", data=dict(GOOD, site="spam"))
            n.assert_not_awaited()

    async def test_rate_limited(self):
        with mock.patch.object(contact.orders, "notify_admins", mock.AsyncMock()):
            codes = [(await self.client.post("/contact", data=GOOD)).status for _ in range(contact.webform.RATE_LIMIT + 1)]
        self.assertEqual(codes[-1], 429)


if __name__ == "__main__":
    unittest.main()
