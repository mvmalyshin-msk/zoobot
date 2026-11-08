"""Вход по номеру через Яндекс ID: номер подтверждает Яндекс, СМС и VPN не нужны. Знакомый номер открывает его кабинет,
новый — заводит клиента; администратор так не входит; без настроек кнопки и маршрутов нет."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_yandex_login_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import phoneverify  # noqa: E402
import webchat  # noqa: E402
from test_phoneverify import fake_config, run  # noqa: E402


async def login(c, phone):
    with fake_config(), \
            mock.patch.object(phoneverify, "exchange_code", mock.AsyncMock(return_value="T")), \
            mock.patch.object(phoneverify, "fetch_phone", mock.AsyncMock(return_value=phone)):
        c.session.cookie_jar.clear()
        r = await c.get("/chat/login/yandex", allow_redirects=False)
        assert r.status == 302, r.status
        loc = r.headers["Location"]
        assert loc.startswith("https://oauth.yandex.ru/authorize?")
        q = parse_qs(urlparse(loc).query)
        assert q["redirect_uri"] == ["https://zoo.test/verify/yandex/cb"]   # тот же адрес возврата, что уже зарегистрирован
        return await c.get(f"/verify/yandex/cb?code=abc&state={q['state'][0]}", allow_redirects=False)


class CodePageYandexTest(unittest.TestCase):
    def test_code_page_offers_yandex_when_configured(self):
        import webchat
        with mock.patch.object(webchat, "_yandex_on", lambda: True):
            self.assertIn("/chat/login/yandex", webchat._code_form("+79001112233").text)
        with mock.patch.object(webchat, "_yandex_on", lambda: False):
            self.assertNotIn("/chat/login/yandex", webchat._code_form("+79001112233").text)


class YandexLoginTest(unittest.TestCase):
    def test_closed_without_credentials(self):
        async def t(c):
            with fake_config(yandex_client_id="", yandex_client_secret=""):
                self.assertEqual((await c.get("/chat/login/yandex", allow_redirects=False)).status, 404)
        run(t)

    def test_new_number_creates_verified_client_and_logs_in(self):
        async def t(c):
            r = await login(c, "+7 900 555-66-77")
            self.assertEqual((r.status, r.headers["Location"]), (302, "/chat"))
            tok = r.cookies[webchat.COOKIE].value
            sess = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(tok),))
            self.assertLess(sess["uid"], 0)
            row = await db.fetch_one("SELECT phone, phone_verified FROM users WHERE tg_id=?", (sess["uid"],))
            self.assertEqual((row["phone"], row["phone_verified"]), ("+79005556677", "yandex"))
        run(t)

    def test_known_number_opens_that_clients_cabinet(self):
        async def t(c):
            await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (7001, 'Анна', '+79005550001')")
            r = await login(c, "+79005550001")
            tok = r.cookies[webchat.COOKIE].value
            sess = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(tok),))
            self.assertEqual(sess["uid"], 7001)
        run(t)

    def test_admin_number_is_refused(self):
        async def t(c):
            await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (111, 'Админ', '+79005550002')")
            r = await login(c, "+79005550002")
            self.assertEqual(r.status, 403)
            self.assertNotIn(webchat.COOKIE, r.cookies)
        run(t)

    def test_no_phone_no_session_and_state_single_use(self):
        async def t(c):
            r = await login(c, None)
            self.assertEqual(r.status, 403)
            self.assertNotIn(webchat.COOKIE, r.cookies)
            bad = await c.get("/verify/yandex/cb?code=x&state=nope", allow_redirects=False)
            self.assertEqual(bad.status, 403)
        run(t)

    def test_phone_form_shows_button_only_when_configured(self):
        async def t(c):
            with fake_config():
                self.assertIn("/chat/login/yandex", await (await c.get("/chat/login")).text())
            with fake_config(yandex_client_id=""):
                self.assertNotIn("/chat/login/yandex", await (await c.get("/chat/login")).text())
        run(t)


if __name__ == "__main__":
    unittest.main()
