"""Уведомления в версии по ссылке (Web Push): приходят, даже когда сайт закрыт, как в Telegram.
Ключи VAPID создаёт и хранит сам сервер; в подписи — адрес сайта, а не почта владельца."""
import asyncio
import base64
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_webpush_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: E402
from cryptography.hazmat.primitives.kdf.hkdf import HKDF  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webpush  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
H = {"X-Zc": "1"}


def b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def run(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM push_subs")
        await db.execute("DELETE FROM web_sessions")
        return await fn()
    return LOOP.run_until_complete(go())


def run_client(fn):
    async def go():
        app = webserver.make_app()
        app["bot"] = None
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return run(go)


# RFC 8291, раздел 5 (пример шифрования сообщения Web Push)
RFC = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "body": "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6P"
            "Bru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN",
}


class EncryptionTest(unittest.TestCase):
    def test_matches_rfc8291_example_byte_for_byte(self):
        as_key = ec.derive_private_key(int.from_bytes(b64(RFC["as_private"]), "big"), ec.SECP256R1())
        body = webpush.encrypt(RFC["plaintext"], RFC["ua_public"], RFC["auth"], salt=b64(RFC["salt"]), eph_key=as_key)
        self.assertEqual(body, b64(RFC["body"]))

    def test_browser_can_decrypt_a_random_message(self):
        ua = ec.generate_private_key(ec.SECP256R1())
        ua_pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        body = webpush.encrypt("Новый заказ №7".encode(), webpush.b64u(ua_pub), webpush.b64u(auth))
        salt, idlen = body[:16], body[20]
        as_pub, ct = body[21:21 + idlen], body[21 + idlen:]
        secret = ua.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_pub))
        ikm = HKDF(hashes.SHA256(), 32, salt=auth, info=b"WebPush: info\x00" + ua_pub + as_pub).derive(secret)
        cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
        nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
        plain = AESGCM(cek).decrypt(nonce, ct, None)
        self.assertEqual(plain.rstrip(b"\x00")[:-1].decode(), "Новый заказ №7")


class VapidTest(unittest.TestCase):
    def test_key_is_created_once_and_kept_in_the_database(self):
        async def go():
            return await webpush.public_key_b64(), await webpush.public_key_b64()
        a, b = run(go)
        self.assertEqual(a, b)
        self.assertEqual(len(b64(a)), 65)

    def test_jwt_is_signed_and_names_the_site_not_an_email(self):
        async def go():
            key = await webpush._vapid_key()
            return key, webpush.vapid_jwt(key, "https://fcm.googleapis.com", "https://zoo.example")
        key, jwt = run(go)
        head, claims, sig = jwt.split(".")
        c = json.loads(b64(claims))
        self.assertEqual(json.loads(b64(head))["alg"], "ES256")
        self.assertEqual(c["aud"], "https://fcm.googleapis.com")
        self.assertEqual(c["sub"], "https://zoo.example")
        self.assertNotIn("@", c["sub"])
        import time as _t
        self.assertLessEqual(c["exp"] - _t.time(), 3600 + 5)  # Apple и Google: подпись живёт не дольше часа
        raw = b64(sig)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        key.public_key().verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))  # не бросает — подпись верна


def _sub(endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    ua = ec.generate_private_key(ec.SECP256R1())
    pub = ua.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"endpoint": endpoint, "keys": {"p256dh": webpush.b64u(pub), "auth": webpush.b64u(os.urandom(16))}}


class SubscribeApiTest(unittest.TestCase):
    def test_client_and_admin_subscriptions_are_stored_for_their_own_uid(self):
        async def go(c):
            ct = await webchat.new_session(7701)
            at = await webchat.new_session(min(config.admin_ids))
            key = await (await c.get("/chat/api/push/key", headers={"Cookie": f"{webchat.COOKIE}={ct}"})).json()
            r1 = await c.post("/chat/api/push/subscribe", json=_sub("https://fcm.googleapis.com/fcm/send/c1"),
                              headers={**H, "Cookie": f"{webchat.COOKIE}={ct}"})
            r2 = await c.post("/portal/bot/api/push/subscribe", json=_sub("https://web.push.apple.com/a1"),
                              headers={**H, "Cookie": f"{webchat.ADMIN_COOKIE}={at}"})
            rows = await db.fetch_all("SELECT uid, endpoint FROM push_subs ORDER BY id")
            return key, r1.status, r2.status, [(r["uid"], r["endpoint"]) for r in rows]
        key, s1, s2, rows = run_client(go)
        self.assertEqual(len(b64(key["key"])), 65)
        self.assertEqual((s1, s2), (200, 200))
        self.assertEqual(rows, [(7701, "https://fcm.googleapis.com/fcm/send/c1"),
                                (min(config.admin_ids), "https://web.push.apple.com/a1")])

    def test_unsafe_endpoints_and_missing_session_are_refused(self):
        async def go(c):
            ct = await webchat.new_session(7702)
            h = {**H, "Cookie": f"{webchat.COOKIE}={ct}"}
            bad = []
            for ep in ("http://fcm.googleapis.com/x", "https://127.0.0.1/x", "https://localhost/x",
                       "https://fcm.googleapis.com:8443/x", "https://10.0.0.5/x", "ftp://x.y/z"):
                bad.append((await c.post("/chat/api/push/subscribe", json=_sub(ep), headers=h)).status)
            anon = (await c.post("/chat/api/push/subscribe", json=_sub(), headers=H)).status
            broken = (await c.post("/chat/api/push/subscribe", json={"endpoint": "https://fcm.googleapis.com/x"},
                                   headers=h)).status
            return bad, anon, broken, await db.fetch_one("SELECT COUNT(*) AS n FROM push_subs")
        bad, anon, broken, count = run_client(go)
        self.assertEqual(set(bad), {400})
        self.assertEqual(anon, 401)
        self.assertEqual(broken, 400)
        self.assertEqual(count["n"], 0)


class WhenToNotifyTest(unittest.TestCase):
    def test_bot_message_notifies_but_own_browser_action_does_not(self):
        async def go():
            with mock.patch.object(webpush, "notify", mock.AsyncMock()) as notify:
                await webchat.push(7703, "send", who="bot", text="Заказ №7 подтверждён")
                await webchat.push(7703, "send", who="me", text="моё сообщение")
                token = webchat.ORIGIN.set(7703)
                try:
                    await webchat.push(7703, "send", who="bot", text="ответ на моё действие")
                finally:
                    webchat.ORIGIN.reset(token)
                await asyncio.sleep(0.05)
                return [c.args for c in notify.await_args_list]
        calls = run(go)
        self.assertEqual(calls, [(7703, "Заказ №7 подтверждён")])


class SendTest(unittest.TestCase):
    def _post_returns(self, status):
        resp = mock.MagicMock(status=status)
        cm = mock.MagicMock()
        cm.__aenter__ = mock.AsyncMock(return_value=resp)
        cm.__aexit__ = mock.AsyncMock(return_value=False)
        session = mock.MagicMock()
        session.post = mock.MagicMock(return_value=cm)
        scm = mock.MagicMock()
        scm.__aenter__ = mock.AsyncMock(return_value=session)
        scm.__aexit__ = mock.AsyncMock(return_value=False)
        return scm, session

    def test_delivered_push_carries_vapid_and_encryption_headers(self):
        async def go():
            await webpush.save_subscription(7704, _sub("https://fcm.googleapis.com/fcm/send/ok"))
            scm, session = self._post_returns(201)
            with mock.patch.object(webpush.aiohttp, "ClientSession", return_value=scm):
                await webpush.notify(7704, "Администратор: добрый день")
            return session.post.call_args
        call = run(go)
        url, headers = call.args[0], call.kwargs["headers"]
        self.assertEqual(url, "https://fcm.googleapis.com/fcm/send/ok")
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertTrue(headers["Authorization"].startswith("vapid t="))
        self.assertIn(", k=", headers["Authorization"])
        self.assertIn("TTL", headers)

    def test_gone_subscription_is_removed(self):
        async def go():
            await webpush.save_subscription(7705, _sub("https://fcm.googleapis.com/fcm/send/gone"))
            scm, _ = self._post_returns(410)
            with mock.patch.object(webpush.aiohttp, "ClientSession", return_value=scm):
                await webpush.notify(7705, "привет")
            return await db.fetch_one("SELECT COUNT(*) AS n FROM push_subs WHERE uid=7705")
        self.assertEqual(run(go)["n"], 0)


class ServiceWorkerTest(unittest.TestCase):
    def test_service_worker_is_served_and_valid_javascript(self):
        async def go(c):
            r = await c.get("/sw.js")
            return r.status, r.headers.get("Content-Type", ""), await r.text()
        status, ctype, body = run_client(go)
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn("addEventListener('push'", body)
        self.assertIn("addEventListener('notificationclick'", body)
        check = subprocess.run(["node", "--check", str(ROOT / "webapp" / "sw.js")], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)

    def test_chat_page_registers_worker_and_subscribes_through_its_own_api(self):
        html = (ROOT / "webapp" / "chat.html").read_text(encoding="utf-8")
        self.assertIn("serviceWorker.register('/sw.js')", html)
        self.assertIn("API+'/api/push/subscribe'", html)
        self.assertIn("API+'/api/push/key'", html)


class DeliveryReportTest(unittest.TestCase):
    """notify() сообщает, что вышло, а журнал показывает успешные отправки: раньше «не пришло» нельзя было разобрать."""

    def _post_returns(self, status):
        return SendTest._post_returns(self, status)

    def test_report_counts_sent_removed_failed(self):
        async def go():
            ok = _sub("https://web.push.apple.com/ok")
            gone = _sub("https://fcm.googleapis.com/fcm/send/gone2")
            await webpush.save_subscription(7710, ok)
            await webpush.save_subscription(7710, gone)
            answers = {"https://web.push.apple.com/ok": 201, "https://fcm.googleapis.com/fcm/send/gone2": 410}
            cms = []
            for status in (201, 410):
                cms.append(self._post_returns(status)[0])
            with mock.patch.object(webpush.aiohttp, "ClientSession", side_effect=cms):
                rep = await webpush.notify(7710, "привет")
            return rep, answers
        rep, _ = run(go)
        self.assertEqual((rep["total"], rep["sent"], rep["removed"], rep["failed"]), (2, 1, 1, 0))

    def test_report_for_user_without_subscriptions(self):
        rep = run(lambda: webpush.notify(7711, "привет"))
        self.assertEqual(rep, {"total": 0, "sent": 0, "removed": 0, "failed": 0})

    def test_network_error_is_failed_not_exception(self):
        async def go():
            await webpush.save_subscription(7712, _sub("https://web.push.apple.com/down"))
            with mock.patch.object(webpush.aiohttp, "ClientSession", side_effect=OSError("down")):
                return await webpush.notify(7712, "привет")
        rep = run(go)
        self.assertEqual((rep["total"], rep["sent"], rep["failed"]), (1, 0, 1))

    def test_successful_send_is_logged(self):
        async def go():
            await webpush.save_subscription(7713, _sub("https://web.push.apple.com/logme"))
            scm, _ = self._post_returns(201)
            with mock.patch.object(webpush.aiohttp, "ClientSession", return_value=scm):
                with self.assertLogs("webpush", level="INFO") as cap:
                    await webpush.notify(7713, "привет")
            return cap.output
        out = " ".join(run(go))
        self.assertIn("web.push.apple.com", out)
        self.assertIn("201", out)


class AdminPushTest(unittest.TestCase):
    """Приложение «ЗК Админ» с экрана «Домой» открывается на /portal: подписка и проверка уведомлений — там же."""

    def test_portal_pages_subscribe_the_device(self):
        async def go(c):
            at = await webchat.new_session(min(config.admin_ids))
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: at})
            return [await (await c.get(p)).text() for p in ("/portal", "/portal/settings", "/portal/clients")]
        for html in run_client(go):
            self.assertIn("serviceWorker.register('/sw.js')", html)
            self.assertIn("/portal/bot/api/push/subscribe", html)
            self.assertIn("/portal/bot/api/push/key", html)
            self.assertIn('id="perm"', html)

    def test_settings_shows_devices_and_test_button(self):
        async def go(c):
            uid = min(config.admin_ids)
            at = await webchat.new_session(uid)
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: at})
            before = await (await c.get("/portal/settings")).text()
            await webpush.save_subscription(7730, _sub("https://fcm.googleapis.com/fcm/send/someone-else"))  # чужие не в счёт
            await webpush.save_subscription(uid, _sub("https://web.push.apple.com/dev1"))
            await webpush.save_subscription(uid, _sub("https://fcm.googleapis.com/fcm/send/dev2"))
            after = await (await c.get("/portal/settings")).text()
            return before, after
        before, after = run_client(go)
        self.assertIn("Уведомления", before)
        self.assertIn("Устройств с уведомлениями: 0", before)
        self.assertIn("Устройств с уведомлениями: 2", after)
        self.assertIn("Проверить уведомления", after)

    def test_test_push_reports_result_to_the_page(self):
        async def go(c):
            uid = min(config.admin_ids)
            at = await webchat.new_session(uid)
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: at})
            none = await (await c.post("/portal/api/push/test", headers=H)).json()
            await webpush.save_subscription(uid, _sub("https://web.push.apple.com/t1"))
            scm, session = SendTest._post_returns(self, 201)
            with mock.patch.object(webpush.aiohttp, "ClientSession", return_value=scm):
                ok = await (await c.post("/portal/api/push/test", headers=H)).json()
            return none, ok, session.post.call_count
        none, ok, calls = run_client(go)
        self.assertEqual((none["total"], none["sent"]), (0, 0))
        self.assertEqual((ok["total"], ok["sent"]), (1, 1))
        self.assertEqual(calls, 1)

    def test_test_push_is_admin_only_and_same_origin(self):
        async def go(c):
            anon = await c.post("/portal/api/push/test", headers=H)
            ct = await webchat.new_session(7720)
            client = await c.post("/portal/api/push/test", headers={**H, "Cookie": f"{webchat.ADMIN_COOKIE}={ct}"})
            at = await webchat.new_session(min(config.admin_ids))
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: at})
            evil = await c.post("/portal/api/push/test", headers={**H, "Origin": "https://evil.example"})
            return anon.status, client.status, evil.status
        self.assertEqual(run_client(go), (403, 403, 403))

    def test_worker_sets_app_badge_and_page_clears_it(self):
        sw = (ROOT / "webapp" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("navigator.setAppBadge();", sw)
        check = subprocess.run(["node", "--check", str(ROOT / "webapp" / "sw.js")], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        import portal
        self.assertIn("clearAppBadge", portal.NOTIFY_JS)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
