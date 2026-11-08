"""Главная страница сервиса (/): три входа, кнопки входа, карта переехала на /map, старые ссылки живы."""
import os
import re
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
        for must in ("Зооконсьерж", "подберёт подходящего специалиста", "Выгул", "Зооняня", "Передержка", "Зоотакси",
                     "несколько вариантов по цене и уровню исполнения"):
            self.assertIn(must, t)

    async def test_three_equal_entries_lead_to_working_points(self):
        t = await self.home()
        cards = re.findall(r'<a class="entry[^"]*"[^>]*href="([^"]+)"', t)
        self.assertEqual(len(cards), 3)
        self.assertIn("https://t.me/zooconcierge_bot", cards)
        self.assertIn("/chat", cards)
        self.assertIn("/request", cards)
        for text in ("Открыть Telegram-бот", "Оставить заявку на сайте", "Заполнить форму"):
            self.assertIn(text, t)

    async def test_login_buttons_are_different_and_not_the_main_thing(self):
        t = await self.home()
        self.assertIn("Войти как клиент", t)
        self.assertIn("Войти как администратор", t)
        client = re.search(r'<a class="login client"[^>]*href="([^"]+)"', t).group(1)
        admin = re.search(r'<a class="login admin"[^>]*href="([^"]+)"', t).group(1)
        self.assertEqual((client, admin), ("/chat/login", "/portal"))
        self.assertLess(t.index("Выгул"), t.index("Войти как клиент"))     # вход — внизу, после услуг и заявок

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
