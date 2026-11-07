"""Подтверждение телефона клиента: номер из Telegram-кнопки подтверждён, набранный и чужой — нет;
веб-клиент подтверждает номер через Telegram; после регистрации в Telegram есть кнопка «Открыть на сайте»."""
import asyncio
import os
import sys
import time
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_phone_test.db")
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import Chat, Message, User  # noqa: E402

import db  # noqa: E402
import repo  # noqa: E402
import texts  # noqa: E402
import webchat  # noqa: E402
from bot import build_dispatcher  # noqa: E402
from handlers.client import Profile  # noqa: E402

TG = []  # то, что ушло бы в Telegram
DP = build_dispatcher()
LOOP = asyncio.new_event_loop()
_upd = [900000]


class Cap(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        TG.append(method)
        return Message(message_id=len(TG), date=datetime.now(),
                       chat=Chat(id=getattr(method, "chat_id", 0), type="private"), text=getattr(method, "text", None))


def run(coro_fn):
    async def go():
        await db.init_db()
        TG.clear()
        bot = Bot("1:x", session=webchat.WebRouterSession(Cap()))
        return await coro_fn(bot)
    return LOOP.run_until_complete(go())


async def new_client(bot, uid: int, state, name: str = "Анна"):
    """Клиент с согласием и состоянием сценария: как будто он уже дошёл до нужного шага."""
    await db.execute("DELETE FROM users WHERE tg_id=?", (uid,))
    user = await repo.get_or_create_user(User(id=uid, is_bot=False, first_name=name))
    await repo.save_consent(user, "pdn", True, texts.PDN_VERSION)
    key = StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid)
    await DP.storage.set_state(key, state.state if hasattr(state, "state") else state)
    await DP.storage.set_data(key, {"name": name, "after": None})
    return key


def _update(uid: int, **body):
    _upd[0] += 1
    msg = {"message_id": _upd[0], "date": int(time.time()), "chat": {"id": uid, "type": "private"},
           "from": {"id": uid, "is_bot": False, "first_name": "Анна"}}
    msg.update(body)
    return {"update_id": _upd[0], "message": msg}


async def feed(bot, uid: int, **body):
    await DP.feed_raw_update(bot, _update(uid, **body))


async def state_of(key):
    return await DP.storage.get_state(key)


async def user_row(uid: int):
    return await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (uid,))


def sent_texts() -> list[str]:
    return [m.text for m in TG if isinstance(m, SendMessage) and m.text]


class PhoneRulesTest(unittest.TestCase):
    def test_typed_number_in_telegram_is_refused(self):
        async def t(bot):
            key = await new_client(bot, 6101, Profile.phone)
            await feed(bot, 6101, text="+7 900 123-45-67")
            self.assertEqual(await state_of(key), Profile.phone.state)
            self.assertIsNone((await user_row(6101))["phone"])
            self.assertIn(texts.PHONE_ONLY_BUTTON, sent_texts())
        run(t)

    def test_foreign_contact_is_refused(self):
        async def t(bot):
            # скриншот: клиент переслал чужую карточку контакта («Удалён аккаунт») — это не его номер
            key = await new_client(bot, 6102, Profile.phone)
            await feed(bot, 6102, contact={"phone_number": "+79100486632", "first_name": "Чужой", "user_id": 777})
            self.assertEqual(await state_of(key), Profile.phone.state)
            self.assertIsNone((await user_row(6102))["phone"])
            self.assertIn(texts.PHONE_NOT_OWN, sent_texts())
        run(t)

    def test_own_contact_is_saved_and_verified_by_telegram(self):
        async def t(bot):
            key = await new_client(bot, 6103, Profile.phone)
            await feed(bot, 6103, contact={"phone_number": "79001234567", "first_name": "Анна", "user_id": 6103})
            self.assertEqual(await state_of(key), Profile.address.state)
            data = await DP.storage.get_data(key)
            self.assertEqual(data["phone"], "+79001234567")
            self.assertEqual(data["phone_verified"], "telegram")
        run(t)

    def test_web_guest_number_is_saved_but_marked_unverified(self):
        async def t(bot):
            uid = -6104  # гость с сайта: номер набран в окошке, Telegram его не подтверждал
            key = await new_client(bot, uid, Profile.phone)
            await feed(bot, uid, contact={"phone_number": "+79007654321", "first_name": "Гость"})
            self.assertEqual(await state_of(key), Profile.address.state)
            data = await DP.storage.get_data(key)
            self.assertEqual(data["phone"], "+79007654321")
            self.assertIsNone(data.get("phone_verified"))
        run(t)

    def test_web_guest_confirms_phone_through_telegram_link(self):
        async def t(bot):
            web = -6105
            await new_client(bot, web, Profile.phone)
            await db.execute("UPDATE users SET phone=?, phone_verified=NULL WHERE tg_id=?", ("+79005550000", web))
            tg = 6106
            key = await new_client(bot, tg, Profile.phone)
            link = await webchat.make_tg_link(web)
            token = link.split("wl_", 1)[1]
            await feed(bot, tg, text="/start wl_" + token,
                       entities=[{"type": "bot_command", "offset": 0, "length": 6}])
            from handlers.client import LinkPhone
            self.assertEqual(await state_of(key), LinkPhone.wait.state)
            await feed(bot, tg, contact={"phone_number": "79005551234", "first_name": "Анна", "user_id": tg})
            self.assertIsNone(await state_of(key))
            row = await user_row(web)
            self.assertEqual(row["phone"], "+79005551234")
            self.assertEqual(row["phone_verified"], "telegram")
        run(t)


class SiteLoginTest(unittest.TestCase):
    def test_telegram_client_gets_one_time_site_button(self):
        async def t(bot):
            import handlers.client as hc
            from types import SimpleNamespace
            from unittest import mock
            fake = SimpleNamespace(webapp_url="https://zoo.test", admin_ids=webchat.config.admin_ids)
            with mock.patch.object(webchat, "config", fake):  # адрес сайта задаётся окружением; в тесте подставляем свой
                markup = await hc.site_login_markup(6107)
            self.assertIsNotNone(markup)
            url = markup.inline_keyboard[0][0].url
            self.assertTrue(url.startswith("https://zoo.test/chat/login?t="))
        run(t)


if __name__ == "__main__":
    unittest.main()
