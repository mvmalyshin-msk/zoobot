"""Подтверждение телефона через Яндекс ID: без настроек закрыто; номер привязывается только к тому гостю,
который начал подтверждение в этом браузере; состояние одноразовое."""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_phoneverify_test.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message, User  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import phoneverify  # noqa: E402
import repo  # noqa: E402
import webchat  # noqa: E402

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
        app = web.Application()
        app["bot"], app["dp"] = bot, None
        phoneverify.setup(app)
        webchat.setup(app)
        async with TestClient(TestServer(app)) as c:
            return await coro_fn(c)
    return LOOP.run_until_complete(go())


def fake_config(**over):
    base = dict(yandex_client_id="cid", yandex_client_secret="sec", webapp_url="https://zoo.test")
    base.update(over)
    return mock.patch.object(phoneverify, "config", SimpleNamespace(**base))


async def new_guest(c) -> int:
    """Новый гость с сайта: cookie выдаётся страницей /chat, клиент в базе уже заведён."""
    c.session.cookie_jar.clear()
    await c.get("/chat")
    uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
    await repo.get_or_create_user(User(id=uid, is_bot=False, first_name="Гость"))
    return uid


class YandexVerifyTest(unittest.TestCase):
    def test_closed_without_credentials(self):
        async def t(c):
            with fake_config(yandex_client_id="", yandex_client_secret=""):
                self.assertEqual((await c.get("/verify/yandex", allow_redirects=False)).status, 404)
        run(t)

    def test_guest_gets_verified_phone_from_yandex_profile(self):
        async def t(c):
            uid = await new_guest(c)
            with fake_config(), \
                    mock.patch.object(phoneverify, "exchange_code", mock.AsyncMock(return_value="TOKEN")) as ex, \
                    mock.patch.object(phoneverify, "fetch_phone", mock.AsyncMock(return_value="+7 900 111-22-33")):
                r = await c.get("/verify/yandex", allow_redirects=False)
                self.assertEqual(r.status, 302)
                loc = r.headers["Location"]
                self.assertTrue(loc.startswith("https://oauth.yandex.ru/authorize?"))
                q = parse_qs(urlparse(loc).query)
                self.assertEqual(q["client_id"], ["cid"])
                self.assertEqual(q["redirect_uri"], ["https://zoo.test/verify/yandex/cb"])
                cb = await c.get(f"/verify/yandex/cb?code=abc&state={q['state'][0]}", allow_redirects=False)
                self.assertEqual(cb.status, 302)
                ex.assert_awaited_once_with("abc")
            row = await db.fetch_one("SELECT phone, phone_verified FROM users WHERE tg_id=?", (uid,))
            self.assertEqual(row["phone"], "+79001112233")
            self.assertEqual(row["phone_verified"], "yandex")
        run(t)

    def test_state_is_bound_to_the_guest_and_single_use(self):
        async def t(c):
            with fake_config(), \
                    mock.patch.object(phoneverify, "exchange_code", mock.AsyncMock(return_value="T")), \
                    mock.patch.object(phoneverify, "fetch_phone", mock.AsyncMock(return_value="+79001112233")):
                first = await new_guest(c)
                loc = (await c.get("/verify/yandex", allow_redirects=False)).headers["Location"]
                state = parse_qs(urlparse(loc).query)["state"][0]
                other = await new_guest(c)  # другой гость пытается забрать чужое подтверждение
                self.assertNotEqual(first, other)
                bad = await c.get(f"/verify/yandex/cb?code=x&state={state}", allow_redirects=False)
                self.assertEqual(bad.status, 403)
                self.assertIsNone((await db.fetch_one("SELECT phone FROM users WHERE tg_id=?", (other,)))["phone"])
                # тот же гость, второй раз с тем же состоянием — отказ (одноразовое)
                c.session.cookie_jar.clear()
                await c.get("/chat")
                again = await c.get(f"/verify/yandex/cb?code=x&state={state}", allow_redirects=False)
                self.assertEqual(again.status, 403)
        run(t)

    def test_no_phone_from_yandex_is_reported_not_saved(self):
        async def t(c):
            uid = await new_guest(c)
            with fake_config(), \
                    mock.patch.object(phoneverify, "exchange_code", mock.AsyncMock(return_value="T")), \
                    mock.patch.object(phoneverify, "fetch_phone", mock.AsyncMock(return_value=None)):
                loc = (await c.get("/verify/yandex", allow_redirects=False)).headers["Location"]
                state = parse_qs(urlparse(loc).query)["state"][0]
                await c.get(f"/verify/yandex/cb?code=x&state={state}", allow_redirects=False)
            row = await db.fetch_one("SELECT phone, phone_verified FROM users WHERE tg_id=?", (uid,))
            self.assertIsNone(row["phone"])
            self.assertIsNone(row["phone_verified"])
        run(t)


if __name__ == "__main__":
    unittest.main()
