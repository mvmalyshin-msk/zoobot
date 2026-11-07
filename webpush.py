"""Уведомления в версии по ссылке (Web Push, RFC 8030 / 8291 / 8292) — без сторонних сервисов.

Браузер подписывается на уведомления (кнопка «🔔 Уведомления» в чате), сервер шифрует сообщение
ключами браузера (aes128gcm) и отправляет его в push-сервис браузера (Google, Apple, Mozilla…).
Уведомление приходит, даже когда сайт закрыт; на iPhone — если сайт добавлен на экран «Домой».

Ключи VAPID сервер создаёт сам при первом запуске и хранит в базе (kv), их не нужно заводить вручную.
В подписи (sub) — адрес сайта, а не почта владельца: почта не уходит во внешние сервисы."""
import base64
import ipaddress
import json
import logging
import os
import time
from urllib.parse import urlparse

import aiohttp
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

import db
from config import config

log = logging.getLogger(__name__)

RECORD_SIZE = 4096
TTL = 24 * 3600            # сколько push-сервис хранит уведомление, если телефон выключен
MAX_BODY = 160             # длина текста в уведомлении
_KV_KEY = "vapid_private_pem"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------- ключи VAPID ----------

async def _vapid_key() -> ec.EllipticCurvePrivateKey:
    row = await db.fetch_one("SELECT value FROM kv WHERE key=?", (_KV_KEY,))
    if row is None:
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        await db.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, ?)", (_KV_KEY, pem))
        row = await db.fetch_one("SELECT value FROM kv WHERE key=?", (_KV_KEY,))  # при гонке берём сохранённый
    return serialization.load_pem_private_key(row["value"].encode(), password=None)


async def public_key_b64() -> str:
    """Открытый ключ для браузера (applicationServerKey)."""
    return b64u(_raw_public(await _vapid_key()))


def vapid_jwt(key: ec.EllipticCurvePrivateKey, audience: str, sub: str, ttl: int = 3600) -> str:
    """JWT ES256 для заголовка Authorization: vapid (RFC 8292)."""
    head = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = b64u(json.dumps({"aud": audience, "exp": int(time.time()) + ttl, "sub": sub},
                             separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())))
    return f"{head}.{claims}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ---------- шифрование (RFC 8291, aes128gcm) ----------

def encrypt(payload: bytes, p256dh: str, auth: str, *, salt: bytes | None = None,
            eph_key: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """Тело запроса к push-сервису. salt и eph_key задаются только в тестах (пример из RFC 8291)."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    eph = eph_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(eph)
    shared = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    ikm = HKDF(hashes.SHA256(), 32, salt=auth_secret,
               info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    salt = salt or os.urandom(16)
    cek = HKDF(hashes.SHA256(), 16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 — последняя (единственная) запись
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


# ---------- подписки ----------

def safe_endpoint(endpoint: str) -> bool:
    """Только https на 443 к имени хоста: сервер не должен слать запросы во внутреннюю сеть по чужой подписке."""
    try:
        u = urlparse(endpoint)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if u.scheme != "https" or not host or u.port not in (None, 443) or len(endpoint) > 1000:
        return False
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return False
    try:
        ipaddress.ip_address(host)
        return False  # адрес вместо имени — не push-сервис
    except ValueError:
        return "." in host


def valid_subscription(sub) -> bool:
    if not isinstance(sub, dict) or not safe_endpoint(str(sub.get("endpoint", ""))):
        return False
    keys = sub.get("keys")
    if not isinstance(keys, dict):
        return False
    try:
        return len(unb64u(str(keys.get("p256dh", "")))) == 65 and len(unb64u(str(keys.get("auth", "")))) == 16
    except (ValueError, TypeError):
        return False


async def save_subscription(uid: int, sub: dict) -> None:
    keys = sub["keys"]
    await db.execute(
        f"""INSERT INTO push_subs (uid, endpoint, p256dh, auth) VALUES (?,?,?,?)
            ON CONFLICT(endpoint) DO UPDATE SET uid=excluded.uid, p256dh=excluded.p256dh, auth=excluded.auth,
            {db.touch()}""",
        (uid, sub["endpoint"], keys["p256dh"], keys["auth"]))


# ---------- отправка ----------

def _site() -> str:
    return config.webapp_url or "https://zoobot-production.up.railway.app"


async def notify(uid: int, text: str) -> None:
    """Уведомление на все браузеры, где человек включил уведомления. Ошибки не роняют бота."""
    subs = await db.fetch_all("SELECT * FROM push_subs WHERE uid=?", (uid,))
    if not subs:
        return
    url = "/portal/bot" if uid in config.admin_ids else "/chat"
    body = (text or "Новое сообщение").strip()
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY - 1] + "…"
    payload = json.dumps({"title": "Зооконсьерж", "body": body, "url": url}, ensure_ascii=False).encode()
    key = await _vapid_key()
    public = b64u(_raw_public(key))
    for sub in subs:
        u = urlparse(sub["endpoint"])
        headers = {
            "TTL": str(TTL),
            "Urgency": "high",
            "Content-Encoding": "aes128gcm",
            "Content-Type": "application/octet-stream",
            "Authorization": f"vapid t={vapid_jwt(key, f'{u.scheme}://{u.netloc}', _site())}, k={public}",
        }
        try:
            data = encrypt(payload, sub["p256dh"], sub["auth"])
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.post(sub["endpoint"], data=data, headers=headers) as r:
                    status = r.status
        except Exception as e:  # сеть или push-сервис недоступны — сообщение всё равно есть в чате
            log.info("Web Push не отправлен (%s): %s", u.netloc, type(e).__name__)
            continue
        if status in (404, 410):  # подписка отозвана или устарела
            await db.execute("DELETE FROM push_subs WHERE id=?", (sub["id"],))
        elif status >= 400:
            log.info("Web Push: %s ответил %s", u.netloc, status)
