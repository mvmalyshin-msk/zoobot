"""Вход администратора по Face ID / Touch ID (WebAuthn, «ключ доступа»).

Биометрия — удобное подтверждение, а не первичная проверка: ключ можно создать только в уже выполненном входе
(код из Telegram или почты), а войти по нему — на том устройстве, где ключ создан. Ключ можно отозвать.
Проверка подписи — вручную на `cryptography` (без новых библиотек); настоящий iPhone в тестах заменён
«поддельным устройством», которое подписывает так же, как Face ID."""
import asyncio
import hashlib
import json
import os
import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_passkeys_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

import db  # noqa: E402
import passkeys  # noqa: E402
import portal  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
H = {"X-Zc": "1"}
b64u, unb64u = passkeys.b64u, passkeys.unb64u


# ---------- «iPhone»: делает всё то же, что Safari и Face ID ----------

def cbor(v) -> bytes:
    """Минимальный кодировщик CBOR для ключа COSE и attestationObject."""
    def head(major, n):
        if n < 24:
            return bytes([major << 5 | n])
        if n < 256:
            return bytes([major << 5 | 24, n])
        return bytes([major << 5 | 25]) + struct.pack(">H", n)
    if isinstance(v, bool):
        return b"\xf5" if v else b"\xf4"
    if isinstance(v, int):
        return head(0, v) if v >= 0 else head(1, -1 - v)
    if isinstance(v, bytes):
        return head(2, len(v)) + v
    if isinstance(v, str):
        return head(3, len(v.encode())) + v.encode()
    if isinstance(v, dict):
        return head(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    if isinstance(v, list):
        return head(4, len(v)) + b"".join(cbor(x) for x in v)
    raise TypeError(v)


class Phone:
    def __init__(self, rp_id="127.0.0.1", origin=None):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = os.urandom(32)
        self.counter = 0
        self.rp_id = rp_id
        self.origin = origin

    def _client(self, kind, challenge, origin=None):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin or self.origin,
                           "crossOrigin": False}).encode()

    def cose(self, alg=-7, kty=2, crv=1) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor({1: kty, 3: alg, -1: crv, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def create(self, challenge, *, origin=None, rp_id=None, flags=0x45, kind="webauthn.create", cose=None) -> dict:
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        auth = (rp_hash + bytes([flags]) + struct.pack(">I", 0) + b"\x00" * 16
                + struct.pack(">H", len(self.cred_id)) + self.cred_id + (cose or self.cose()))
        att = cbor({"fmt": "none", "attStmt": {}, "authData": auth})
        return {"id": b64u(self.cred_id), "response": {
            "clientDataJSON": b64u(self._client(kind, challenge, origin)), "attestationObject": b64u(att)}}

    def get(self, challenge, *, origin=None, rp_id=None, flags=0x05, counter=None, kind="webauthn.get",
            key=None) -> dict:
        self.counter = counter if counter is not None else self.counter + 1
        rp_hash = hashlib.sha256((rp_id or self.rp_id).encode()).digest()
        auth = rp_hash + bytes([flags]) + struct.pack(">I", self.counter)
        client = self._client(kind, challenge, origin)
        sig = (key or self.key).sign(auth + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        return {"id": b64u(self.cred_id), "response": {
            "clientDataJSON": b64u(client), "authenticatorData": b64u(auth), "signature": b64u(sig)}}


def run(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM passkeys")
        await db.execute("DELETE FROM web_sessions")
        passkeys._challenges.clear()
        portal._fails.clear()
        portal._pk_hits.clear()
        app = webserver.make_app()
        app["bot"] = None
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return LOOP.run_until_complete(go())


async def login_admin(c, device_ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"):
    tok = await webchat.new_session(111, device="iPhone · Safari")
    c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
    return tok


def origin_of(c):
    return f"http://{c.server.host}:{c.server.port}"


async def register(c, phone=None):
    """Админ в уже выполненном входе создаёт ключ на «iPhone»."""
    phone = phone or Phone(rp_id=c.server.host, origin=origin_of(c))
    opts = await (await c.post("/portal/api/passkey/options", headers=H)).json()
    cred = phone.create(opts["publicKey"]["challenge"])
    r = await c.post("/portal/api/passkey/register", json=cred, headers=H)
    return phone, r, opts


async def login_options(c):
    return (await (await c.post("/portal/passkey/options", headers=H)).json())["publicKey"]


class ChallengeTest(unittest.TestCase):
    def test_single_use_purpose_and_owner(self):
        passkeys._challenges.clear()
        c = passkeys.new_challenge("reg", 111)
        self.assertFalse(passkeys.take_challenge(c, "login", 111), "другое назначение")
        c = passkeys.new_challenge("reg", 111)
        self.assertFalse(passkeys.take_challenge(c, "reg", 222), "другой человек")
        c = passkeys.new_challenge("reg", 111)
        self.assertTrue(passkeys.take_challenge(c, "reg", 111))
        self.assertFalse(passkeys.take_challenge(c, "reg", 111), "второй раз нельзя")
        self.assertFalse(passkeys.take_challenge("нет такой", "reg", 111))

    def test_expires_and_memory_is_bounded(self):
        passkeys._challenges.clear()
        c = passkeys.new_challenge("login")
        t, p, u = passkeys._challenges[c]
        passkeys._challenges[c] = (t - passkeys.CHALLENGE_TTL - 1, p, u)
        self.assertFalse(passkeys.take_challenge(c, "login", None))
        for _ in range(passkeys.MAX_CHALLENGES + 50):
            passkeys.new_challenge("login")
        self.assertLessEqual(len(passkeys._challenges), passkeys.MAX_CHALLENGES)
        passkeys._challenges.clear()


class CborTest(unittest.TestCase):
    def test_roundtrip_of_what_phones_send(self):
        obj = {"fmt": "none", "attStmt": {}, "authData": b"\x01\x02", 1: 2, -2: b"x" * 32, "n": [1, -5, True]}
        self.assertEqual(passkeys.cbor_decode(cbor(obj))[0], obj)

    def test_garbage_is_rejected_not_crash(self):
        for bad in (b"", b"\xff", b"\xa1", b"\x5f", b"\x59\xff\xff", b"\xbf"):
            with self.assertRaises(passkeys.PasskeyError):
                passkeys.cbor_decode(bad)


class RegistrationTest(unittest.TestCase):
    def test_admin_registers_key_and_it_is_listed(self):
        async def go(c):
            await login_admin(c)
            phone, r, opts = await register(c)
            row = await db.fetch_one("SELECT * FROM passkeys")
            html = await (await c.get("/portal/settings")).text()
            return r.status, opts, row, html, phone
        status, opts, row, html, phone = run(go)
        self.assertEqual(status, 200)
        pk = opts["publicKey"]
        self.assertEqual(pk["pubKeyCredParams"], [{"type": "public-key", "alg": -7}])
        self.assertEqual(pk["authenticatorSelection"]["userVerification"], "required")
        self.assertEqual(pk["authenticatorSelection"]["authenticatorAttachment"], "platform")
        self.assertEqual(pk["attestation"], "none")
        self.assertEqual(row["uid"], 111)
        self.assertEqual(row["cred_id"], b64u(phone.cred_id))
        self.assertEqual(len(unb64u(row["public_key"])), 65)
        self.assertIn("Face ID", html)
        self.assertIn("iPhone", html)

    def test_registration_needs_admin_session_and_same_origin(self):
        async def go(c):
            anon = await c.post("/portal/api/passkey/options", headers=H)
            ct = await webchat.new_session(7777)
            client = await c.post("/portal/api/passkey/options", headers={**H, "Cookie": f"{webchat.ADMIN_COOKIE}={ct}"})
            await login_admin(c)
            evil = await c.post("/portal/api/passkey/options", headers={**H, "Origin": "https://evil.example"})
            return anon.status, client.status, evil.status
        self.assertEqual(run(go), (403, 403, 403))

    def test_bad_registrations_are_refused(self):
        cases = {
            "чужой сайт": lambda p, ch, o: p.create(ch, origin="https://evil.example"),
            "чужой rpId": lambda p, ch, o: p.create(ch, rp_id="evil.example"),
            "не тот challenge": lambda p, ch, o: p.create("AAAA"),
            "без проверки Face ID (UV)": lambda p, ch, o: p.create(ch, flags=0x41),
            "без присутствия (UP)": lambda p, ch, o: p.create(ch, flags=0x44),
            "не создание ключа": lambda p, ch, o: p.create(ch, kind="webauthn.get"),
            "ключ RSA, а не ES256": lambda p, ch, o: p.create(ch, cose=p.cose(alg=-257)),
            "не та кривая": lambda p, ch, o: p.create(ch, cose=p.cose(crv=2)),
            "не тип EC2": lambda p, ch, o: p.create(ch, cose=p.cose(kty=3)),
        }

        async def go(c):
            await login_admin(c)
            bad = {}
            for name, make in cases.items():
                opts = await (await c.post("/portal/api/passkey/options", headers=H)).json()
                phone = Phone(rp_id=c.server.host, origin=origin_of(c))
                r = await c.post("/portal/api/passkey/register", json=make(phone, opts["publicKey"]["challenge"], None), headers=H)
                if r.status == 200:
                    bad[name] = r.status
            n = (await db.fetch_one("SELECT COUNT(*) AS n FROM passkeys"))["n"]
            return bad, n
        bad, n = run(go)
        self.assertEqual(bad, {})
        self.assertEqual(n, 0)

    def test_challenge_is_single_use(self):
        async def go(c):
            await login_admin(c)
            phone = Phone(rp_id=c.server.host, origin=origin_of(c))
            opts = await (await c.post("/portal/api/passkey/options", headers=H)).json()
            cred = phone.create(opts["publicKey"]["challenge"])
            first = await c.post("/portal/api/passkey/register", json=cred, headers=H)
            second = await c.post("/portal/api/passkey/register", json=cred, headers=H)
            return first.status, second.status
        self.assertEqual(run(go), (200, 400))

    def test_garbage_body_is_400_not_500(self):
        async def go(c):
            await login_admin(c)
            await c.post("/portal/api/passkey/options", headers=H)
            out = []
            for body in ({}, {"id": 1}, {"id": "x", "response": "y"}, {"id": "x", "response": {"clientDataJSON": "!!", "attestationObject": "!!"}}):
                out.append((await c.post("/portal/api/passkey/register", json=body, headers=H)).status)
            raw = await c.post("/portal/api/passkey/register", data=b"not json", headers=H)
            return out + [raw.status]
        self.assertEqual(run(go), [400] * 5)


class LoginTest(unittest.TestCase):
    def test_face_id_login_gives_admin_session_with_device_name(self):
        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            c.session.cookie_jar.clear()  # «другой раз»: cookie нет, но на телефоне ключ есть
            before = await c.get("/portal")
            opts = await login_options(c)
            r = await c.post("/portal/passkey/login", json=phone.get(opts["challenge"]),
                             headers={**H, "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1"})
            after = await c.get("/portal")
            sess = await db.fetch_one("SELECT uid, device FROM web_sessions ORDER BY id DESC LIMIT 1")
            return (await before.text()), r.status, await after.text(), dict(sess), opts
        before, status, after, sess, opts = run(go)
        self.assertIn("Вход в портал", before)
        self.assertEqual(status, 200)
        self.assertNotIn("Вход в портал", after)
        self.assertEqual(sess, {"uid": 111, "device": "iPhone · Safari"})
        self.assertEqual(opts["userVerification"], "required")

    def test_bad_assertions_do_not_log_in(self):
        wrong_key = ec.generate_private_key(ec.SECP256R1())
        cases = {
            "чужая подпись": lambda p, ch: p.get(ch, key=wrong_key),
            "чужой сайт": lambda p, ch: p.get(ch, origin="https://evil.example"),
            "чужой rpId": lambda p, ch: p.get(ch, rp_id="evil.example"),
            "не тот challenge": lambda p, ch: p.get("AAAA"),
            "без Face ID (UV)": lambda p, ch: p.get(ch, flags=0x01),
            "не вход": lambda p, ch: p.get(ch, kind="webauthn.create"),
        }

        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            c.session.cookie_jar.clear()
            bad = {}
            for name, make in cases.items():
                opts = await login_options(c)
                r = await c.post("/portal/passkey/login", json=make(phone, opts["challenge"]), headers=H)
                if r.status == 200:
                    bad[name] = r.status
            return bad, "Вход в портал" in await (await c.get("/portal")).text()
        bad, locked = run(go)
        self.assertEqual(bad, {})
        self.assertTrue(locked)

    def test_challenge_cannot_be_replayed(self):
        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            c.session.cookie_jar.clear()
            opts = await login_options(c)
            body = phone.get(opts["challenge"])
            first = await c.post("/portal/passkey/login", json=body, headers=H)
            c.session.cookie_jar.clear()
            second = await c.post("/portal/passkey/login", json=body, headers=H)
            return first.status, second.status
        self.assertEqual(run(go), (200, 400))

    def test_unknown_or_revoked_key_is_refused(self):
        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            row = await db.fetch_one("SELECT id FROM passkeys")
            r = await c.post("/portal/settings", data={"step": "pk_revoke", "id": str(row["id"])}, allow_redirects=False)
            c.session.cookie_jar.clear()
            opts = await login_options(c)
            after = await c.post("/portal/passkey/login", json=phone.get(opts["challenge"]), headers=H)
            stranger = Phone(rp_id=c.server.host, origin=origin_of(c))
            opts = await login_options(c)
            unknown = await c.post("/portal/passkey/login", json=stranger.get(opts["challenge"]), headers=H)
            return r.status, after.status, unknown.status, (await db.fetch_one("SELECT COUNT(*) AS n FROM passkeys"))["n"]
        self.assertEqual(run(go), (303, 400, 400, 0))

    def test_signature_counter_must_not_go_back(self):
        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            c.session.cookie_jar.clear()
            results = []
            for counter in (5, 6, 3):  # третий — клон ключа или повтор
                opts = await login_options(c)
                r = await c.post("/portal/passkey/login", json=phone.get(opts["challenge"], counter=counter), headers=H)
                results.append(r.status)
                c.session.cookie_jar.clear()
            return results
        self.assertEqual(run(go), [200, 200, 400])

    def test_key_of_non_admin_never_logs_in(self):
        async def go(c):
            await login_admin(c)
            phone, _, _ = await register(c)
            await db.execute("UPDATE passkeys SET uid=7777")  # ключ «чужого» человека
            c.session.cookie_jar.clear()
            opts = await login_options(c)
            r = await c.post("/portal/passkey/login", json=phone.get(opts["challenge"]), headers=H)
            return r.status, "Вход в портал" in await (await c.get("/portal")).text()
        status, locked = run(go)
        self.assertEqual(status, 400)
        self.assertTrue(locked)

    def test_login_attempts_are_rate_limited(self):
        async def go(c):
            codes = []
            for _ in range(portal.FAIL_LIMIT + 3):
                await login_options(c)
                r = await c.post("/portal/passkey/login", json={"id": "x", "response": {}}, headers=H)
                codes.append(r.status)
            return codes
        codes = run(go)
        self.assertIn(429, codes)


class PagesTest(unittest.TestCase):
    def test_settings_has_face_id_card_and_revoke(self):
        async def go(c):
            await login_admin(c)
            before = await (await c.get("/portal/settings")).text()
            await register(c)
            after = await (await c.get("/portal/settings")).text()
            return before, after
        before, after = run(go)
        self.assertIn("Face ID", before)
        self.assertIn("Включить вход по Face ID", before)
        self.assertIn("navigator.credentials.create", before)
        self.assertIn('name="step" value="pk_revoke"', after)

    def test_revoke_only_own_admin_keys(self):
        async def go(c):
            await login_admin(c)
            await register(c)
            await db.execute("INSERT INTO passkeys (uid, cred_id, public_key, device) VALUES (7777, 'zzz', 'k', 'чужой')")
            foreign = (await db.fetch_one("SELECT id FROM passkeys WHERE uid=7777"))["id"]
            await c.post("/portal/settings", data={"step": "pk_revoke", "id": str(foreign)}, allow_redirects=False)
            return (await db.fetch_one("SELECT COUNT(*) AS n FROM passkeys WHERE uid=7777"))["n"]
        self.assertEqual(run(go), 1)

    def test_login_page_offers_face_id_only_when_device_has_a_key(self):
        async def go(c):
            return await (await c.get("/portal")).text()
        html = run(go)
        self.assertIn("navigator.credentials.get", html)
        self.assertIn("zc_passkey", html)          # показываем кнопку, если ключ создан на этом устройстве
        self.assertIn("/portal/passkey/options", html)
        self.assertIn("Войти по Face ID", html)

    def test_scripts_are_valid_javascript(self):
        import re
        import subprocess
        for name, html in (("login", portal.login_page()), ("settings", portal.PASSKEY_JS)):
            for i, m in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S) or [html]):
                js = m if name == "login" else re.sub(r"</?script>", "", m)
                path = f"/tmp/pk_{name}_{i}.js"
                Path(path).write_text(js, encoding="utf-8")
                check = subprocess.run(["node", "--check", path], capture_output=True, text=True)
                self.assertEqual(check.returncode, 0, check.stderr)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
