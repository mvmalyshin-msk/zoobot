import asyncio
import os
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_webchat_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import BufferedInputFile, Chat, InlineKeyboardButton, InlineKeyboardMarkup, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from bot import build_dispatcher  # noqa: E402
from config import config  # noqa: E402

TG = []  # то, что ушло бы в настоящий Telegram


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        TG.append(method)
        return Message(message_id=len(TG), date=datetime.now(), chat=Chat(id=getattr(method, "chat_id", 0), type="private"),
                       text=getattr(method, "text", None))


H = {"X-Zc": "1"}


DP = build_dispatcher()  # роутеры в проекте общие на процесс, поэтому диспетчер один на все тесты
LOOP = asyncio.new_event_loop()


def run(coro_fn):
    async def go():
        await db.init_db()
        for tbl in ("web_sessions", "web_events", "web_login", "web_files"):
            await db.execute(f"DELETE FROM {tbl}")
        webchat._linked_cache.clear()
        TG.clear()
        bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, DP
        async with TestClient(TestServer(app)) as c:
            return await coro_fn(c, bot)
    return LOOP.run_until_complete(go())


async def events(c, after=0):
    r = await c.get(f"/chat/api/poll?after={after}")
    return (await r.json())["events"]


async def history(c):
    return await (await c.get("/chat/api/history")).json()


class GuestTest(unittest.TestCase):
    def test_page_sets_cookie_and_guest_has_negative_id(self):
        async def t(c, bot):
            r = await c.get("/chat")
            self.assertEqual(r.status, 200)
            self.assertIn("zcw", r.cookies)
            self.assertTrue(r.cookies["zcw"]["httponly"])
            uid = await webchat.session_uid(_Req(r.cookies["zcw"].value))
            self.assertLess(uid, 0)
        run(t)

    def test_api_requires_session_and_header(self):
        async def t(c, bot):
            self.assertEqual((await c.get("/chat/api/history")).status, 401)
            await c.get("/chat")
            r = await c.post("/chat/api/send", json={"text": "/start"})
            self.assertEqual(r.status, 403)
        run(t)

    def test_start_gets_reply_in_browser_and_nothing_in_telegram(self):
        async def t(c, bot):
            await c.get("/chat")
            r = await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            self.assertEqual(r.status, 200)
            h = await history(c)
            who = [m["who"] for m in h["messages"]]
            self.assertEqual(who[0], "me")
            self.assertIn("bot", who)
            self.assertEqual(TG, [])
            self.assertFalse(h["admin"])
        run(t)

    def test_button_click_comes_back_as_events(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            h = await history(c)
            btn = next(b for m in h["messages"] for row in m.get("inline", []) for b in row)
            mid = next(m["mid"] for m in h["messages"] if m.get("inline"))
            before = h["last"]
            r = await c.post("/chat/api/send", json={"cb": btn["cb"], "mid": mid}, headers=H)
            self.assertEqual(r.status, 200)
            self.assertTrue(await events(c, before))
        run(t)

    def test_rate_limit(self):
        async def t(c, bot):
            await c.get("/chat")
            codes = set()
            for _ in range(webchat.SEND_LIMIT + 3):
                codes.add((await c.post("/chat/api/send", json={"text": "привет"}, headers=H)).status)
            self.assertIn(429, codes)
        webchat._hits.clear()
        run(t)

    def test_empty_and_garbage(self):
        async def t(c, bot):
            await c.get("/chat")
            self.assertEqual((await c.post("/chat/api/send", json={"text": "  "}, headers=H)).status, 400)
            self.assertEqual((await c.post("/chat/api/send", data="не json", headers=H)).status, 400)
        run(t)


class _Req:
    def __init__(self, token):
        self.cookies = {webchat.COOKIE: token}


class AdminLoginTest(unittest.TestCase):
    def test_login_flow_one_time(self):
        async def t(c, bot):
            link = await webchat.make_login_link(111)
            tok = re.search(r"t=(.+)$", link).group(1)
            page = await c.get(f"/chat/login?t={tok}")
            self.assertEqual(page.status, 200)
            self.assertIn(tok, await page.text())  # GET ничего не сжигает
            r = await c.post("/chat/login", data={"t": tok}, allow_redirects=False)
            self.assertEqual(r.status, 302)
            self.assertEqual((await history(c))["admin"], True)
            again = await c.post("/chat/login", data={"t": tok}, allow_redirects=False)
            self.assertEqual(again.status, 403)
        run(t)

    def test_client_may_open_own_cabinet_but_not_portal(self):
        async def t(c, bot):
            link = await webchat.make_login_link(999)  # клиент из Telegram: в свой кабинет — можно
            tok = re.search(r"t=(.+)$", link).group(1)
            self.assertEqual((await c.post("/chat/login", data={"t": tok}, allow_redirects=False)).status, 302)
            self.assertEqual((await history(c))["admin"], False)
            link = await webchat.make_login_link(999)  # а на портал — нет
            tok = re.search(r"t=(.+)$", link).group(1)
            self.assertEqual((await c.post("/chat/login", data={"t": tok, "n": "portal"}, allow_redirects=False)).status, 403)
            link = await webchat.make_login_link(-5)  # гость с сайта никогда не получает такую ссылку
            tok = re.search(r"t=(.+)$", link).group(1)
            self.assertEqual((await c.post("/chat/login", data={"t": tok}, allow_redirects=False)).status, 403)
            link = await webchat.make_login_link(111)
            tok2 = re.search(r"t=(.+)$", link).group(1)
            await db.execute("UPDATE web_login SET expires_at=datetime('now','-1 minutes')")
            self.assertEqual((await c.post("/chat/login", data={"t": tok2}, allow_redirects=False)).status, 403)
            self.assertEqual((await c.post("/chat/login", data={"t": "x"}, allow_redirects=False)).status, 403)
        run(t)

    def test_login_page_escapes_token(self):
        async def t(c, bot):
            page = await (await c.get("/chat/login?t=%27%3E%3Cscript%3E")).text()
            self.assertNotIn("<script>", page)
        run(t)

    def test_logout_kills_session(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.get("/chat/logout", allow_redirects=False)
            self.assertEqual((await c.get("/chat/api/history")).status, 401)
        run(t)


class RoutingTest(unittest.TestCase):
    def test_admin_notification_goes_to_telegram_and_browser_when_logged_in(self):
        async def t(c, bot):
            await orders.notify(bot, 111, "без браузера")
            self.assertEqual(len(TG), 1)
            link = await webchat.make_login_link(111)
            await c.post("/chat/login", data={"t": re.search(r"t=(.+)$", link).group(1)})
            await orders.notify(bot, 111, "новая заявка")
            await asyncio.sleep(0.05)
            self.assertEqual(len(TG), 2)
            texts = [m["text"] for m in (await history(c))["messages"]]
            self.assertIn("новая заявка", texts)
            self.assertNotIn("без браузера", texts)
        run(t)

    def test_guest_notification_never_reaches_telegram(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions"))["uid"]
            await orders.notify(bot, uid, "напоминание")
            self.assertEqual(TG, [])
            self.assertIn("напоминание", [m["text"] for m in (await history(c))["messages"]])
        run(t)

    def test_document_for_browser_user_is_downloadable_only_by_owner(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions"))["uid"]
            await bot.send_document(uid, BufferedInputFile(b"hello", filename="отчёт.xlsx"), caption="файл")
            m = next(m for m in (await history(c))["messages"] if m.get("file"))
            r = await c.get(f"/chat/file/{m['file']['id']}")
            self.assertEqual(await r.read(), b"hello")
            self.assertIn("UTF-8", r.headers["Content-Disposition"])
            other = await db.execute("INSERT INTO web_files (uid, name, data) VALUES (-9999, 'x', x'00')")
            self.assertEqual((await c.get(f"/chat/file/{other}")).status, 404)
        run(t)

    def test_edit_updates_history(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions"))["uid"]
            sent = await bot.send_message(uid, "было", reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="Ок", callback_data="x"),
                                  InlineKeyboardButton(text="Карта", url="https://e.com")]]))
            await bot.edit_message_text("стало", chat_id=uid, message_id=sent.message_id)
            m = next(m for m in (await history(c))["messages"] if m["mid"] == sent.message_id)
            self.assertEqual(m["text"], "стало")
            self.assertEqual(m["inline"][0][1]["url"], "https://e.com")
        run(t)


class ChatRelayTest(unittest.TestCase):
    def test_client_text_reaches_admin_and_admin_reply_reaches_client(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC"))["uid"]
            await c.post("/chat/api/send", json={"cb": "pdn:agree", "mid": 1}, headers=H)
            await c.post("/chat/api/send", json={"text": "/menu"}, headers=H)
            TG.clear()
            await c.post("/chat/api/send", json={"text": "Когда вы приедете?"}, headers=H)
            to_admin = [m for m in TG if isinstance(m, SendMessage) and m.chat_id == 111]
            self.assertTrue(any("Когда вы приедете?" in m.text for m in to_admin), [getattr(m, "text", m) for m in TG])
            btn = to_admin[-1].reply_markup.inline_keyboard[0][0]
            self.assertEqual(btn.callback_data, f"wc:{uid}")
            app = c.server.app
            await webchat.feed(app, 111, cb=btn.callback_data, mid=1)
            await webchat.feed(app, 111, text="Приедем в 10:00")
            texts = [m["text"] for m in (await history(c))["messages"]]
            self.assertTrue(any("Приедем в 10:00" in x for x in texts), texts)
            await webchat.feed(app, 111, cb="wcx", mid=1)
            n = len((await history(c))["messages"])
            await webchat.feed(app, 111, text="это уже не клиенту")
            self.assertEqual(len((await history(c))["messages"]), n)
        run(t)


class TelegramVerifyTest(unittest.TestCase):
    """П.4: клиент с сайта подтверждает себя через Telegram — по одноразовой ссылке t.me/бот?start=wl_…"""

    def test_link_verifies_and_duplicates_notifications(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC"))["uid"]
            url = await webchat.make_tg_link(uid)
            self.assertTrue(url.startswith(f"https://t.me/{config.bot_username}?start=wl_"), url)
            payload = url.split("start=", 1)[1]
            self.assertLessEqual(len(payload), 64, "Telegram принимает не больше 64 знаков")
            TG.clear()
            await DP.feed_raw_update(bot, _tg_update(9001, f"/start {payload}"))
            row = await db.fetch_one("SELECT verified_tg_id FROM users WHERE tg_id=?", (uid,))
            self.assertEqual(row["verified_tg_id"], 9001)
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == 9001 and "подтвержд" in m.text.lower() for m in TG))
            self.assertTrue(any("подтвержд" in (m.get("text") or "").lower() for m in (await history(c))["messages"]),
                            "в чате на сайте тоже видно, что подтверждено")
            TG.clear()
            await orders.notify(bot, uid, "Заказ №5 подтверждён")
            await asyncio.sleep(0.05)
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == 9001 and "Заказ №5" in m.text for m in TG),
                            "уведомления теперь дублируются в Telegram")
            TG.clear()
            await DP.feed_raw_update(bot, _tg_update(9002, f"/start {payload}"))
            self.assertEqual((await db.fetch_one("SELECT verified_tg_id FROM users WHERE tg_id=?", (uid,)))["verified_tg_id"], 9001,
                             "ссылка одноразовая: чужой не перепривяжет")
        run(t)

    def test_expired_link(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC"))["uid"]
            url = await webchat.make_tg_link(uid)
            await db.execute("UPDATE web_links SET expires_at=datetime('now','-1 minutes')")
            await DP.feed_raw_update(bot, _tg_update(9003, "/start " + url.split("start=", 1)[1]))
            self.assertIsNone((await db.fetch_one("SELECT verified_tg_id FROM users WHERE tg_id=?", (uid,)))["verified_tg_id"])
        run(t)

    def test_web_registration_offers_telegram_confirmation(self):
        async def t(c, bot):
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            await c.post("/chat/api/send", json={"cb": "pdn:agree", "mid": 1}, headers=H)
            for step in ("menu:order",):
                await c.post("/chat/api/send", json={"cb": step, "mid": 1}, headers=H)
            for text in ("Вера", "+7 900 222-33-44"):
                await c.post("/chat/api/send", json={"text": text}, headers=H)
            uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC"))["uid"]
            await db.execute("UPDATE users SET address='Мира 1', address_verified=1 WHERE tg_id=?", (uid,))
            import handlers.client as hc
            from aiogram.fsm.context import FSMContext
            from aiogram.fsm.storage.base import StorageKey
            st = FSMContext(DP.storage, StorageKey(bot_id=bot.id, chat_id=uid, user_id=uid))
            await st.update_data(name="Вера", phone="+79002223344")
            msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private")).as_(bot)
            from aiogram.types import User
            await hc.finish_profile(msg, st, User(id=uid, is_bot=False, first_name="Вера"))
            h = await history(c)
            urls = [b.get("url") for m in h["messages"] for row in m.get("inline", []) for b in row if b.get("url")]
            self.assertTrue(any(u.startswith(f"https://t.me/{config.bot_username}?start=wl_") for u in urls), urls)
        run(t)


def _tg_update(uid, text):
    return {"update_id": abs(uid) + int(datetime.now().timestamp()) % 100000, "message": {
        "message_id": 1, "date": int(datetime.now().timestamp()), "chat": {"id": uid, "type": "private"},
        "from": {"id": uid, "is_bot": False, "first_name": "Тест"}, "text": text,
        "entities": [{"type": "bot_command", "offset": 0, "length": 6}]}}


class UrlTest(unittest.TestCase):
    def test_chat_url_for_web_client_is_relay_marker(self):
        self.assertEqual(orders.chat_url(None, -5), "web:-5")
        self.assertTrue(orders.chat_url("ann", 5).startswith("https://t.me/"))

    def test_keyboard_turns_marker_into_callback(self):
        import keyboards as kb
        mk = kb.chat_button("web:-5")
        b = mk.inline_keyboard[0][0]
        self.assertEqual(b.callback_data, "wc:-5")
        self.assertIsNone(b.url)



class WebPhoneTest(unittest.TestCase):
    """Регистрация по номеру в вебе: сессия, вошедшая по ссылке из Telegram, тоже вводит номер в чате."""

    def test_telegram_linked_session_can_type_phone_and_registration_moves_on(self):
        async def t(c, bot):
            from aiogram.fsm.storage.base import StorageKey
            from aiogram.types import User
            import repo
            import texts
            from handlers.client import Profile
            tg = 7755
            await db.execute("DELETE FROM users WHERE tg_id=?", (tg,))
            user = await repo.get_or_create_user(User(id=tg, is_bot=False, first_name="Максим"))
            await repo.save_consent(user, "pdn", True, texts.PDN_VERSION)
            token = await webchat.new_session(tg)
            headers = dict(H, Cookie=f"{webchat.COOKIE}={token}")
            key = StorageKey(bot_id=bot.id, chat_id=tg, user_id=tg)
            await DP.storage.set_state(key, Profile.phone.state)
            await DP.storage.update_data(key, {"name": "Максим"})
            r = await c.post("/chat/api/send", json={"text": "+7 900 123-45-67"}, headers=headers)
            self.assertEqual(r.status, 200)
            self.assertNotEqual(await DP.storage.get_state(key), Profile.phone.state)
            self.assertEqual((await DP.storage.get_data(key)).get("phone"), "+79001234567")
        run(t)



class ClientWebCopyTest(unittest.TestCase):
    """Сообщение клиенту (ответ админа, статус заказа) приходит и в Telegram, и в чат на сайте, если клиент им пользуется."""

    async def _web_texts(self, uid):
        rows = await db.fetch_all("SELECT payload FROM web_events WHERE uid=? AND op='send'", (uid,))
        return " ".join(r["payload"] for r in rows)

    def test_admin_reply_reaches_telegram_client_in_site_chat_too(self):
        async def t(c, bot):
            tg = 7801
            await webchat.new_session(tg)  # клиент открыл «Открыть на сайте»: сессия сайта с его Telegram ID
            await bot.send_message(tg, "👤 Администратор: Добрый день")
            self.assertIn("Добрый день", await self._web_texts(tg))
            await asyncio.sleep(0.05)
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == tg and "Добрый день" in m.text for m in TG))
        run(t)

    def test_admin_reply_reaches_linked_guest_chat(self):
        async def t(c, bot):
            tg = 7802
            await c.get("/chat")
            guest = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
            await db.execute("INSERT INTO users (tg_id, verified_tg_id) VALUES (?, ?)", (guest, tg))
            webchat._tg_cache.clear()
            await bot.send_message(tg, "👤 Администратор: Что с кошкой")
            self.assertIn("Что с кошкой", await self._web_texts(guest))
            await asyncio.sleep(0.05)
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == tg for m in TG))
        run(t)

    def test_client_without_site_chat_gets_telegram_only(self):
        async def t(c, bot):
            tg = 7803
            await bot.send_message(tg, "только Telegram")
            self.assertEqual(await self._web_texts(tg), "")
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == tg for m in TG))
        run(t)

    def test_linking_sends_each_channel_its_own_message_once(self):
        """Привязка сайта к Telegram: в Telegram — «аккаунт с сайта подтверждён», на сайте — «подтверждено через Telegram».
        Копирование не должно удваивать их в обоих каналах."""
        async def t(c, bot):
            import texts
            await c.get("/chat")
            await c.post("/chat/api/send", json={"text": "/start"}, headers=H)
            guest = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
            url = await webchat.make_tg_link(guest)
            TG.clear()
            await DP.feed_raw_update(bot, _tg_update(7805, "/start " + url.split("start=", 1)[1]))
            await asyncio.sleep(0.05)
            web = await self._web_texts(guest)
            tg_texts = [m.text for m in TG if isinstance(m, SendMessage) and m.chat_id == 7805]
            self.assertEqual(web.count("Подтверждено через Telegram"), 1, web)
            self.assertNotIn("Ваш аккаунт с сайта подтверждён", web)
            self.assertEqual(sum(texts.TG_LINK_OK_TG in x for x in tg_texts), 1, tg_texts)
            self.assertFalse(any(texts.TG_LINK_OK_WEB in x for x in tg_texts), tg_texts)
        run(t)

    def test_clients_own_telegram_dialogue_is_not_mirrored_to_site(self):
        async def t(c, bot):
            tg = 7806
            await webchat.new_session(tg)
            await DP.feed_raw_update(bot, _tg_update(tg, "/start"))  # клиент сам пишет боту в Telegram
            await asyncio.sleep(0.05)
            self.assertEqual(await self._web_texts(tg), "")
            self.assertTrue(any(isinstance(m, SendMessage) and m.chat_id == tg for m in TG))
            await bot.send_message(tg, "👤 Администратор: а это копируем")  # пришло со стороны — копируем
            self.assertIn("а это копируем", await self._web_texts(tg))
        run(t)

    def test_telegram_failure_still_leaves_message_on_site(self):
        async def t(c, bot):
            tg = 7804
            await webchat.new_session(tg)
            from unittest import mock
            with mock.patch.object(Inner, "make_request", mock.AsyncMock(side_effect=RuntimeError("telegram down"))):
                await bot.send_message(tg, "👤 Администратор: важное")
            self.assertIn("важное", await self._web_texts(tg))
        run(t)


if __name__ == "__main__":
    unittest.main()
