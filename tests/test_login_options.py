"""Способы входа клиента на сайт, выбранные владельцем 14.11.2026:
1. Код из Telegram в окне на сайте (бот присылает 6 цифр после «Запустить»).
2. Код на почту: Gmail (SMTP) или Brevo; Resend без своего домена клиентам писать не может — такую почту не считаем.
3. Яндекс ID: состояние входа в базе (переживает перезапуск), без номера — вход по почте из Яндекса.
4. Face ID (ключ доступа) для повторного входа клиента; ключ администратора клиентом не входит и наоборот.
5. Кнопки входа — единого аккуратного вида, не крупные.
Адреса, номера и ключи в тесте вымышленные."""
import dataclasses
import hashlib
import importlib
import json
import os
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_login_options_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import db  # noqa: E402
import mailer  # noqa: E402
import passkeys  # noqa: E402
import phoneverify  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

MAIL_ENV = ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_PORT", "SMTP_FROM",
            "BREVO_API_KEY", "BREVO_FROM", "RESEND_API_KEY", "RESEND_FROM")


def no_mail_env():
    return mock.patch.dict(os.environ, {k: "" for k in MAIL_ENV})


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM pending_logins")
        await db.execute("DELETE FROM passkeys")
        passkeys._challenges.clear()
        self.app = webserver.make_app()
        self.app["bot"] = None
        self.client = TestClient(TestServer(self.app))
        await self.client.start_server()
        self.origin = f"http://{self.client.host}:{self.client.port}"

    async def asyncTearDown(self):
        await self.client.close()

    async def post(self, path, data):
        return await self.client.post(path, data=data, allow_redirects=False, headers={"Origin": self.origin})

    async def tg_token(self):
        t = await (await self.client.get("/chat/login/tg")).text()
        return t, re.search(r"start=lg_([\w-]+)", t).group(1)

    async def session_uid(self, resp):
        tok = resp.cookies[webchat.COOKIE].value
        return (await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(tok),)))["uid"]


# ---------- 1. Код из Telegram ----------

class TelegramCode(Base):
    async def test_waiting_page_has_code_window(self):
        t, _ = await self.tg_token()
        self.assertIn('action="/chat/login/tgcode"', t)
        self.assertIn('name="code"', t)
        self.assertIn("autocomplete=one-time-code", t)

    async def test_bot_gets_six_digits_and_they_log_in(self):
        _, token = await self.tg_token()
        code = await webchat.confirm_tg_login(token, 5001)
        self.assertRegex(code, r"^\d{6}$")
        r = await self.post("/chat/login/tgcode", {"t": token, "code": code})
        self.assertEqual((r.status, r.headers.get("Location")), (302, "/chat"))
        self.assertEqual(await self.session_uid(r), 5001)
        again = await self.post("/chat/login/tgcode", {"t": token, "code": code})
        self.assertNotIn(webchat.COOKIE, again.cookies)                       # код одноразовый

    async def test_token_taken_from_cookie_when_form_has_none(self):
        _, token = await self.tg_token()
        code = await webchat.confirm_tg_login(token, 5002)
        r = await self.post("/chat/login/tgcode", {"code": code})             # cookie zcl от страницы ожидания
        self.assertEqual(r.status, 302)

    async def test_code_before_confirmation_and_wrong_codes(self):
        _, token = await self.tg_token()
        t = await (await self.post("/chat/login/tgcode", {"t": token, "code": "123456"})).text()
        self.assertIn("Сначала нажмите «Запустить»", t)
        code = await webchat.confirm_tg_login(token, 5003)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            r = await self.post("/chat/login/tgcode", {"t": token, "code": wrong})
            self.assertNotIn(webchat.COOKIE, r.cookies)
        r = await self.post("/chat/login/tgcode", {"t": token, "code": code})  # после 5 ошибок код сгорает
        self.assertNotIn(webchat.COOKIE, r.cookies)

    async def test_admin_gets_no_code(self):
        _, token = await self.tg_token()
        self.assertFalse(await webchat.confirm_tg_login(token, 111))

    async def test_bot_message_shows_the_code(self):
        self.assertIn("123456", webchat.tg_login_done_text("123456"))

    async def test_cross_site_post_refused(self):
        r = await self.client.post("/chat/login/tgcode", data={"code": "1"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(r.status, 403)


# ---------- 2. Почта ----------

class Mail(unittest.IsolatedAsyncioTestCase):
    def test_who_can_receive_client_codes(self):
        with no_mail_env():
            self.assertFalse(mailer.client_mail_on())
        with no_mail_env(), mock.patch.dict(os.environ, {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "a@gmail.com",
                                                        "SMTP_PASSWORD": "x"}):
            self.assertTrue(mailer.client_mail_on())
        with no_mail_env(), mock.patch.dict(os.environ, {"BREVO_API_KEY": "k", "BREVO_FROM": "a@gmail.com"}):
            self.assertTrue(mailer.client_mail_on())
        with no_mail_env(), mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}):
            self.assertFalse(mailer.client_mail_on())                         # resend.dev пишет только владельцу
            self.assertTrue(mailer.configured())                              # но код админу на его почту уходит
        with no_mail_env(), mock.patch.dict(os.environ, {"RESEND_API_KEY": "k", "RESEND_FROM": "Зоо <onboarding@resend.dev>"}):
            self.assertFalse(mailer.client_mail_on())                         # тестовый адрес Resend, даже если указан явно
        with no_mail_env(), mock.patch.dict(os.environ, {"RESEND_API_KEY": "k", "RESEND_FROM": "Зоо <hi@zoo.example>"}):
            self.assertTrue(mailer.client_mail_on())

    def test_brevo_payload(self):
        with mock.patch.dict(os.environ, {"BREVO_FROM": "a@gmail.com"}):
            p = mailer.brevo_payload("c@example.com", "Тема", "Текст")
        self.assertEqual(p["sender"]["email"], "a@gmail.com")
        self.assertEqual(p["to"], [{"email": "c@example.com"}])
        self.assertEqual((p["subject"], p["textContent"]), ("Тема", "Текст"))

    async def test_smtp_blocked_falls_back_to_brevo(self):
        env = {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "a@gmail.com", "SMTP_PASSWORD": "x",
               "BREVO_API_KEY": "k", "BREVO_FROM": "a@gmail.com"}
        brevo = mock.AsyncMock()
        with no_mail_env(), mock.patch.dict(os.environ, env), \
                mock.patch.object(mailer, "_send_sync", side_effect=OSError("blocked")), \
                mock.patch.object(mailer, "_brevo", brevo):
            ok, why = await mailer.send_checked("c@example.com", "s", "b")
        self.assertTrue(ok, why)
        brevo.assert_awaited_once()

    async def test_smtp_goes_first_when_it_works(self):
        env = {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "a@gmail.com", "SMTP_PASSWORD": "x", "RESEND_API_KEY": "k"}
        resend = mock.AsyncMock()
        with no_mail_env(), mock.patch.dict(os.environ, env), \
                mock.patch.object(mailer, "_send_sync") as smtp, mock.patch.object(mailer, "_resend", resend):
            ok, _ = await mailer.send_checked("c@example.com", "s", "b")
        self.assertTrue(ok)
        smtp.assert_called_once()
        resend.assert_not_awaited()


class MailOnLoginPage(Base):
    async def test_email_form_appears_with_gmail_settings_only(self):
        with no_mail_env():
            self.assertNotIn("/chat/login/email", await (await self.client.get("/chat/login")).text())
        with no_mail_env(), mock.patch.dict(os.environ, {"RESEND_API_KEY": "k"}):
            self.assertNotIn("/chat/login/email", await (await self.client.get("/chat/login")).text())
        with no_mail_env(), mock.patch.dict(os.environ, {"SMTP_HOST": "smtp.gmail.com", "SMTP_USER": "a@gmail.com",
                                                        "SMTP_PASSWORD": "x"}):
            self.assertIn('action="/chat/login/email"', await (await self.client.get("/chat/login")).text())


# ---------- 3. Яндекс ID ----------

def yandex_config():
    return mock.patch.object(phoneverify, "config", dataclasses.replace(
        config, yandex_client_id="id", yandex_client_secret="secret", webapp_url="https://zoo.test"))


class Yandex(Base):
    async def start(self):
        with yandex_config():
            r = await self.client.get("/chat/login/yandex", allow_redirects=False)
        return parse_qs(urlparse(r.headers["Location"]).query)["state"][0]

    async def back(self, state, phone=None, email=None):
        with yandex_config(), mock.patch.object(webchat, "config", dataclasses.replace(config, webapp_url="https://zoo.test")), \
                mock.patch.object(phoneverify, "exchange_code", mock.AsyncMock(return_value="T")), \
                mock.patch.object(phoneverify, "fetch_phone", mock.AsyncMock(return_value=phone)), \
                mock.patch.object(phoneverify, "fetch_email", mock.AsyncMock(return_value=email)):
            return await self.client.get(f"/verify/yandex/cb?code=abc&state={state}", allow_redirects=False)

    async def test_login_survives_restart(self):
        state = await self.start()
        importlib.reload(phoneverify)                                          # пересборка: память процесса пустая
        r = await self.back(state, phone="+79005550101")
        self.assertEqual((r.status, r.headers.get("Location")), (302, "/chat"))

    async def test_without_phone_logs_in_by_yandex_email(self):
        r = await self.back(await self.start(), phone=None, email="Ya.Client@yandex.ru")
        self.assertEqual(r.status, 302)
        uid = await self.session_uid(r)
        row = await db.fetch_one("SELECT email FROM users WHERE tg_id=?", (uid,))
        self.assertEqual(row["email"], "ya.client@yandex.ru")
        r2 = await self.back(await self.start(), phone=None, email="ya.client@yandex.ru")
        self.assertEqual(await self.session_uid(r2), uid)                      # тот же кабинет во второй раз

    async def test_new_client_session_belongs_to_that_client(self):
        r = await self.back(await self.start(), phone="+79005550102")
        uid = await self.session_uid(r)
        row = await db.fetch_one("SELECT phone FROM users WHERE tg_id=?", (uid,))
        self.assertEqual(row["phone"], "+79005550102")


# ---------- 4. Face ID для клиента ----------

def cbor(v) -> bytes:
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        return bytes([major << 5 | 25]) + struct.pack(">H", n)
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(v)


class Phone:
    """Поддельный iPhone: подписывает так же, как Face ID."""

    def __init__(self, rp_id, origin):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.counter = 0
        self.rp_id, self.origin = rp_id, origin

    def _client(self, kind, challenge):
        return json.dumps({"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}).encode()

    def create(self, challenge):
        n = self.key.public_key().public_numbers()
        cose = cbor({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})
        auth = (hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0) + b"\x00" * 16
                + struct.pack(">H", len(self.cred_id)) + self.cred_id + cose)
        att = cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": passkeys.b64u(self.cred_id), "response": {
            "clientDataJSON": passkeys.b64u(self._client("webauthn.create", challenge)),
            "attestationObject": passkeys.b64u(att)}}

    def get(self, challenge):
        self.counter += 1
        auth = hashlib.sha256(self.rp_id.encode()).digest() + bytes([0x05]) + struct.pack(">I", self.counter)
        client = self._client("webauthn.get", challenge)
        sig = self.key.sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": passkeys.b64u(self.cred_id), "response": {
            "clientDataJSON": passkeys.b64u(client), "authenticatorData": passkeys.b64u(auth),
            "signature": passkeys.b64u(sig)}}


H = {"X-Zc": "1"}


class FaceId(Base):
    def phone(self):
        return Phone(self.client.host, self.origin)

    async def as_client(self, uid=6001):
        tok = await webchat.new_session(uid)
        self.client.session.cookie_jar.update_cookies({webchat.COOKIE: tok})

    async def register(self, phone):
        opts = await (await self.client.post("/chat/api/passkey/options", headers={**H, "Origin": self.origin})).json()
        return await self.client.post("/chat/api/passkey/register", json=phone.create(opts["publicKey"]["challenge"]),
                                      headers={**H, "Origin": self.origin})

    async def login(self, phone):
        opts = await (await self.client.post("/chat/login/passkey/options", headers={**H, "Origin": self.origin})).json()
        return await self.client.post("/chat/login/passkey", json=phone.get(opts["publicKey"]["challenge"]),
                                      headers={**H, "Origin": self.origin})

    async def test_client_turns_on_face_id_and_logs_in_with_it(self):
        await self.as_client(6001)
        p = self.phone()
        self.assertEqual((await self.register(p)).status, 200)
        self.client.session.cookie_jar.clear()                                 # в другой раз: cookie нет, ключ на телефоне
        r = await self.login(p)
        self.assertEqual(r.status, 200)
        self.assertEqual(await self.session_uid(r), 6001)

    async def test_guest_without_login_cannot_create_key(self):
        await self.client.get("/chat")                                         # просто гость
        r = await self.client.post("/chat/api/passkey/options", headers={**H, "Origin": self.origin})
        self.assertEqual(r.status, 403)

    async def test_admin_key_never_opens_client_cabinet(self):
        p = self.phone()
        await passkeys.add(111, p.cred_id, p.key.public_key().public_bytes(
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).Encoding.X962,
            __import__("cryptography.hazmat.primitives.serialization", fromlist=["x"]).PublicFormat.UncompressedPoint),
            "iPhone")
        r = await self.login(p)
        self.assertNotEqual(r.status, 200)
        self.assertNotIn(webchat.COOKIE, r.cookies)

    async def test_client_key_never_opens_portal(self):
        await self.as_client(6002)
        p = self.phone()
        await self.register(p)
        self.client.session.cookie_jar.clear()
        opts = await (await self.client.post("/portal/passkey/options", headers={**H, "Origin": self.origin})).json()
        r = await self.client.post("/portal/passkey/login", json=p.get(opts["publicKey"]["challenge"]),
                                   headers={**H, "Origin": self.origin})
        self.assertNotEqual(r.status, 200)
        self.assertNotIn(webchat.ADMIN_COOKIE, r.cookies)

    async def test_pages_offer_face_id(self):
        t = await (await self.client.get("/chat/login")).text()
        self.assertIn('id="pklogin"', t)                                       # кнопка на входе (видна, где браузер умеет)
        self.assertIn("/chat/login/passkey", t)
        await self.as_client(6003)
        chat = await (await self.client.get("/chat")).text()
        self.assertIn('id="fid"', chat)                                        # предложение включить Face ID в чате
        self.assertIn("/chat/api/passkey/register", chat)


# ---------- 5. Кнопки ----------

class Buttons(Base):
    async def test_login_buttons_are_one_compact_style(self):
        with mock.patch.object(webchat, "_yandex_on", lambda: True), mock.patch.object(webchat, "_mail_on", lambda: True):
            t = await (await self.client.get("/chat/login")).text()
        self.assertIn(".auth-btn{", t)
        css = re.search(r"\.auth-btn\{([^}]*)\}", t).group(1)
        h = int(re.search(r"height:(\d+)px", css).group(1))
        self.assertTrue(40 <= h <= 48, h)                                       # не крупные, но удобные пальцу (≥ 40)
        for cls in ("auth-btn tg", "auth-btn ya", "auth-btn mail", "auth-btn fid"):
            self.assertIn(cls, t)
        self.assertNotIn("✈️", t)                                              # аккуратные значки вместо эмодзи


if __name__ == "__main__":
    unittest.main()
