"""Главная страница сервиса: понятный основной CTA, услуги и общая публичная навигация."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_home_test.db"

import db  # noqa: E402
import keyboards as kb  # noqa: E402
import webserver  # noqa: E402


class HomeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def home(self):
        r = await self.client.get("/")
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        return await r.text()

    async def test_home_text_and_services(self):
        t = await self.home()
        for must in ("Зооконсьерж", "Подберём специалиста для прогулки", "Выгул", "Зооняня", "Передержка", "Зоотакси",
                     "варианты по цене"):
            self.assertIn(must, t)

    async def test_home_has_one_primary_action_and_clear_secondary_path(self):
        t = await self.home()
        self.assertIn('<a class="btn" href="/request">Подобрать специалиста</a>', t)
        self.assertIn('href="https://t.me/zooconcierge_bot"', t)
        self.assertIn('href="/contact"', t)
        self.assertEqual(t.count('class="hero-actions"'), 1)

    async def test_public_header_and_footer_replace_internal_ribbon(self):
        t = await self.home()
        self.assertIn('class="site-header"', t)
        self.assertIn('class="site-footer"', t)
        self.assertIn('href="/#services"', t)
        self.assertIn('href="/#how-it-works"', t)
        self.assertNotIn("КЛИЕНТСКОЕ МЕНЮ", t)
        self.assertNotIn("__HEADER__", t)
        self.assertNotIn("__FOOTER__", t)

    async def test_client_and_admin_login_links_are_visible_on_home(self):
        t = await self.home()
        self.assertIn('<a href="/chat/login">Войти как клиент</a>', t)
        self.assertIn('<a href="/portal">Войти как администратор</a>', t)
        self.assertIn('src="/images/hero-mascot.png"', t)
        self.assertIn('alt="Персонаж Зооконсьержа с собакой"', t)

    async def test_original_mascot_image_is_served_locally(self):
        response = await self.client.get("/images/hero-mascot.png")
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "image/png")
        self.assertTrue((await response.read()).startswith(b"\x89PNG\r\n\x1a\n"))

    async def test_home_presents_services_and_process(self):
        t = await self.home()
        self.assertIn("Забота о питомце — без суеты", t)
        self.assertIn('id="services"', t)
        self.assertIn('id="how-it-works"', t)
        for path in ("/vygul-sobak", "/zoonyanya", "/peredergka", "/zootaksi"):
            self.assertIn(f'href="{path}"', t)

    async def test_home_is_light_and_does_not_load_telegram_or_map(self):
        t = await self.home()
        self.assertNotIn("telegram-web-app.js", t)
        self.assertNotIn("leaflet", t)
        self.assertNotIn("https://telegram.org/js", t)

    async def test_telegram_launch_of_old_map_button_goes_to_map(self):
        t = await self.home()
        self.assertIn("tgWebAppData", t)       # старая кнопка карты в уже отправленных сообщениях открывает «/»
        self.assertIn("/map", t)

    async def test_map_lives_on_map_and_keeps_working(self):
        r = await self.client.get("/map")
        self.assertEqual(r.status, 200)
        t = await r.text()
        self.assertIn("telegram-web-app.js", t)
        self.assertIn("Выбрать этот адрес", t)
        self.assertIn("no-cache", r.headers.get("Cache-Control", ""))
        self.assertEqual((await self.client.get("/vendor/leaflet.js")).status, 200)

    async def test_old_links_still_work(self):
        for path in ("/request", "/chat", "/policy", "/healthz", "/portal", "/cabinet"):
            r = await self.client.get(path, allow_redirects=False)
            self.assertIn(r.status, (200, 303, 302), path)


class MapButtonTest(unittest.TestCase):
    def test_map_button_opens_map_page(self):
        with mock.patch.object(kb, "config", type("C", (), {"webapp_url": "https://zoo.test"})()):
            markup = kb.address_ask()
        urls = [b.web_app.url for row in markup.keyboard for b in row if getattr(b, "web_app", None)]
        self.assertEqual(urls, ["https://zoo.test/map"])


if __name__ == "__main__":
    unittest.main()
