"""Пересборка входа клиента на сайт.

Что ломалось у клиентов:
1. Ожидающий вход и коды жили в памяти процесса: перезапуск или пересборка (Amvera) — и «Код не подошёл»,
   а «Войти через Telegram» висело на «Ждём подтверждения…».
2. На iPhone Safari при возврате из Telegram может перезагрузить вкладку: страница заводила новый токен,
   а подтверждён был старый — вход не происходил.
3. Форма «по почте» показывалась, когда почта не настроена: человек вводил адрес и получал ошибку.
4. Кода нет — нет и кнопки «Отправить ещё раз».
Адреса и номера в тесте вымышленные."""
import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_login_rebuild_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import mailer  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

PHONE = "+79161230001"
MAIL = "rebuild@example.com"


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()
        await db.execute("DELETE FROM users WHERE tg_id=777 OR email IS NOT NULL")
        await db.execute("INSERT INTO users (tg_id, phone, phone_verified) VALUES (777, ?, 'telegram')", (PHONE,))
        await db.execute("DELETE FROM pending_logins")                      # у каждого теста своя пауза перед повторным кодом
        self.bot = FakeBot()
        self.mails = []
        await self.boot()

    async def boot(self):
        from aiohttp.test_utils import TestClient, TestServer
        app = webserver.make_app()
        app["bot"] = self.bot
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def restart(self):
        """Как пересборка на хостинге: процесс новый, память пустая, база та же."""
        jar = self.client.session.cookie_jar
        cookies = {c.key: c.value for c in jar}
        await self.client.close()
        importlib.reload(webchat)
        await self.boot()
        self.client.session.cookie_jar.update_cookies(cookies)

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, path, data):
        return await self.client.post(path, data=data, allow_redirects=False, headers={"Origin": self.origin})


class SurvivesRestart(Base):
    async def test_telegram_login_confirmed_after_restart(self):
        t = await (await self.client.get("/chat/login/tg")).text()
        token = re.search(r"start=lg_([\w-]+)", t).group(1)
        await self.restart()
        self.assertTrue(await webchat.confirm_tg_login(token, 555))
        r = await self.client.get(f"/chat/login/tg/poll?t={token}")
        self.assertTrue((await r.json())["ok"])
        self.assertIn(webchat.COOKIE, r.cookies)

    async def test_phone_code_works_after_restart(self):
        await self.post("/chat/login/phone", {"phone": PHONE})
        code = re.search(r"\b(\d{6})\b", self.bot.sent[-1][1]).group(1)
        await self.restart()
        r = await self.post("/chat/login/code", {"phone": PHONE, "code": code})
        self.assertEqual((r.status, r.headers.get("Location")), (302, "/chat"))

    async def test_email_code_works_after_restart(self):
        async def fake_send(to, subject, body):
            self.mails.append(body)
            return True
        with mock.patch.object(mailer, "client_mail_on", return_value=True), \
                mock.patch.object(mailer, "send", side_effect=fake_send):
            await self.post("/chat/login/email", {"email": MAIL})
            code = re.search(r"\b(\d{6})\b", self.mails[-1]).group(1)
            await self.restart()
            r = await self.post("/chat/login/emailcode", {"email": MAIL, "code": code})
        self.assertEqual((r.status, r.headers.get("Location")), (302, "/chat"))


class TelegramWaitingPage(Base):
    async def test_reload_keeps_the_same_pending_login(self):
        a = await (await self.client.get("/chat/login/tg")).text()
        b = await (await self.client.get("/chat/login/tg")).text()             # Safari перезагрузил вкладку
        ta = re.search(r"start=lg_([\w-]+)", a).group(1)
        tb = re.search(r"start=lg_([\w-]+)", b).group(1)
        self.assertEqual(ta, tb)

    async def test_other_browser_gets_its_own_login(self):
        a = await (await self.client.get("/chat/login/tg")).text()
        self.client.session.cookie_jar.clear()
        b = await (await self.client.get("/chat/login/tg")).text()
        self.assertNotEqual(re.search(r"start=lg_([\w-]+)", a).group(1), re.search(r"start=lg_([\w-]+)", b).group(1))

    async def test_page_rechecks_when_user_comes_back(self):
        t = await (await self.client.get("/chat/login/tg")).text()
        self.assertIn("visibilitychange", t)                                      # вернулся из Telegram — проверяем сразу
        self.assertIn("pageshow", t)

    async def test_bot_answer_has_button_to_open_cabinet(self):
        import dataclasses
        with mock.patch.object(webchat, "config", dataclasses.replace(config, webapp_url="https://zoo.example")):
            markup = await webchat.tg_login_done_markup(555)
        url = markup.inline_keyboard[0][0].url
        self.assertTrue(url.startswith("https://zoo.example/chat/login?t="))
        r = await self.client.get("/chat/login?" + url.split("?", 1)[1])
        self.assertEqual(r.status, 200)                                            # ссылка рабочая: страница «Войти»


class LoginPageShowsOnlyWorkingWays(Base):
    async def test_no_email_form_when_mail_not_set_up(self):
        with mock.patch.object(mailer, "client_mail_on", return_value=False):
            t = await (await self.client.get("/chat/login")).text()
        self.assertNotIn("/chat/login/email", t)
        self.assertIn("/chat/login/tg", t)

    async def test_email_form_when_mail_set_up(self):
        with mock.patch.object(mailer, "client_mail_on", return_value=True):
            t = await (await self.client.get("/chat/login")).text()
        self.assertIn('action="/chat/login/email"', t)
        self.assertLess(t.index("/chat/login/tg"), t.index("/chat/login/email"))


class CodePages(Base):
    async def test_phone_code_page_offers_resend_and_autofill(self):
        t = await (await self.post("/chat/login/phone", {"phone": PHONE})).text()
        self.assertIn("Отправить код ещё раз", t)
        self.assertIn('action="/chat/login/phone"', t)
        self.assertIn("autocomplete=one-time-code", t)
        self.assertIn("autofocus", t)

    async def test_email_code_page_offers_resend(self):
        with mock.patch.object(mailer, "client_mail_on", return_value=True), \
                mock.patch.object(mailer, "send", return_value=True):
            t = await (await self.post("/chat/login/email", {"email": MAIL})).text()
        self.assertIn("Отправить код ещё раз", t)
        self.assertIn('action="/chat/login/email"', t)


if __name__ == "__main__":
    unittest.main()
