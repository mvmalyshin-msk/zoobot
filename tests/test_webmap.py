"""Адрес на сайте: кнопки карты и геопозиции доходят до тех же обработчиков, что и в Telegram;
зеркальные ссылки между Telegram и сайтом."""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_webmap_test.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import keyboards as kb  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from bot import build_dispatcher  # noqa: E402
from handlers.client import Profile  # noqa: E402

DP = build_dispatcher()
LOOP = asyncio.new_event_loop()


class Cap(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=0, type="private"))


def run(coro_fn):
    async def go():
        await db.init_db()
        for tbl in ("web_sessions", "web_events"):
            await db.execute(f"DELETE FROM {tbl}")
        bot = Bot("1:x", session=webchat.WebRouterSession(Cap()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, DP
        async with TestClient(TestServer(app)) as c:
            return await coro_fn(c, bot)
    return LOOP.run_until_complete(go())


class MarkupForWebTest(unittest.TestCase):
    def test_map_and_location_buttons_reach_the_browser(self):
        with mock.patch.object(kb, "config", type("C", (), {"webapp_url": "https://zoo.test"})()):
            markup = kb.address_ask()
        flat = [b for row in webchat.serialize_markup(markup)["reply"] for b in row]
        self.assertIn({"t": "🗺 Найти на карте", "map": "https://zoo.test"}, flat)
        self.assertIn({"t": "📍 Отправить геопозицию", "loc": True}, flat)


class WebPickTest(unittest.TestCase):
    def test_pick_from_map_moves_the_browser_client_on(self):
        async def t(c, bot):
            await c.get("/chat")
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
            import repo, texts
            from aiogram.types import User
            user = await repo.get_or_create_user(User(id=uid, is_bot=False, first_name="Гость"))
            await repo.save_consent(user, "pdn", True, texts.PDN_VERSION)  # без согласия бот сначала покажет экран согласия
            key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
            await DP.storage.set_state(key, Profile.address.state)
            await DP.storage.set_data(key, {"name": "Гость", "phone": "+79001112233", "after": None})
            payload = json.dumps({"lat": 55.9, "lon": 37.4, "label": "Химки, Железнодорожная, 33", "flat": ""})
            resp = await c.post("/chat/api/send", headers={"X-Zc": "1"}, json={"pick": payload})
            self.assertEqual(resp.status, 200)
            self.assertNotEqual(await DP.storage.get_state(key), Profile.address.state)
        run(t)

    def test_location_from_browser_is_accepted(self):
        async def t(c, bot):
            await c.get("/chat")
            resp = await c.post("/chat/api/send", headers={"X-Zc": "1"}, json={"loc": {"lat": 55.9, "lon": 37.4}})
            self.assertEqual(resp.status, 200)
        run(t)

    def test_bad_coordinates_are_refused(self):
        async def t(c, bot):
            await c.get("/chat")
            for bad in ({"lat": "x", "lon": 1}, {"lat": 1e9, "lon": 37}, {"lat": None}):
                resp = await c.post("/chat/api/send", headers={"X-Zc": "1"}, json={"loc": bad})
                self.assertEqual(resp.status, 400, bad)
        run(t)


class MirrorLinksTest(unittest.TestCase):
    def test_web_chat_header_links_to_telegram(self):
        async def t(c, bot):
            page = await (await c.get("/chat")).text()
            self.assertIn(f"https://t.me/{webchat.config.bot_username}", page)
            self.assertNotIn("__TG_URL__", page)
        run(t)


if __name__ == "__main__":
    unittest.main()
