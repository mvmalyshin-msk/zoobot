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
        from datetime import datetime
        from config import config
        noon = datetime.now(config.tz).replace(hour=12, minute=0, second=0, microsecond=0)   # днём: показываем номер, не зависим от часов запуска
        self._clock = mock.patch.object(contact, "_now", return_value=noon)
        self._clock.start()

    async def asyncTearDown(self):
        self._clock.stop()
        await self.client.close()
        await db.execute("DELETE FROM web_requests")      # общая база тестов: не оставляем заявки другим

    async def test_home_chat_button_leads_straight_to_contact(self):
        t = await (await self.client.get("/")).text()
        self.assertIn('href="/contact">Связаться</a>', t)                 # в подвале; лишний блок с главной убран (14.11)

    async def test_home_buttons_stack_evenly_on_phone(self):
        t = await (await self.client.get("/")).text()
        self.assertIn(".hero-actions{display:flex", t)
        self.assertIn("@media(max-width:640px)", t)
        self.assertIn(".hero-actions{flex-direction:column;align-items:stretch", t)

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


class PhoneHoursTest(unittest.IsolatedAsyncioTestCase):
    """Номер показываем только с 09:00 до 22:00; ночью — ни кнопки, ни номера, вместо них окно обратной связи."""
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        app = webserver.make_app()
        app["bot"] = mock.MagicMock()
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    def at(self, h, m=0):
        from datetime import datetime
        from config import config
        return datetime.now(config.tz).replace(hour=h, minute=m, second=0, microsecond=0)

    async def page(self, h, m=0):
        with mock.patch.object(contact, "ADMIN_PHONE", "+7 977 816-46-60"), mock.patch.object(contact, "_now", return_value=self.at(h, m)):
            return await (await self.client.get("/contact")).text()

    async def test_daytime_shows_call_button(self):
        for h, m in ((9, 0), (12, 0), (21, 59)):
            self.assertIn('href="/contact/call"', await self.page(h, m), (h, m))

    async def test_night_hides_call_button_and_explains(self):
        for h, m in ((22, 0), (23, 30), (3, 0), (8, 59)):
            t = await self.page(h, m)
            self.assertNotIn("/contact/call", t, (h, m))
            for leak in ("816-46-60", "9778164660", "tel:"):
                self.assertNotIn(leak, t)
            self.assertIn("с 09:00 до 22:00", t)
            self.assertIn("Оставьте сообщение", t)

    async def test_call_url_does_not_dial_at_night(self):
        with mock.patch.object(contact, "ADMIN_PHONE", "+7 977 816-46-60"), mock.patch.object(contact, "_now", return_value=self.at(23)):
            r = await self.client.get("/contact/call", allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertEqual(r.headers["Location"], "/contact")

    async def test_call_url_dials_in_daytime(self):
        with mock.patch.object(contact, "ADMIN_PHONE", "+7 977 816-46-60"), mock.patch.object(contact, "_now", return_value=self.at(12)):
            r = await self.client.get("/contact/call", allow_redirects=False)
        self.assertTrue(r.headers["Location"].startswith("tel:"))


if __name__ == "__main__":
    unittest.main()
