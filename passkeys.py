"""Вход по Face ID / Touch ID (WebAuthn, «ключ доступа») — без сторонних библиотек, на `cryptography`.

Как это работает. В уже выполненном входе (код из Telegram или почты) администратор нажимает «Включить вход по
Face ID»: телефон создаёт пару ключей, секретный остаётся в защищённой памяти телефона и открывается только лицом
или отпечатком, открытый приходит сюда. Потом вместо кода сервер присылает случайную строку (challenge), телефон
подписывает её после Face ID, а мы проверяем подпись открытым ключом. Пароль или код при этом не передаются.

Что проверяется (WebAuthn, уровень 2): тип операции, свою же случайную строку (одноразовую, живёт 5 минут),
адрес сайта (origin), хеш домена (rpIdHash), флаги «человек рядом» (UP) и «проверен Face ID» (UV),
подпись ES256 (P-256), счётчик подписей не идёт назад. Аттестацию (кто сделал телефон) не проверяем и не запрашиваем.
Войти по ключу может только администратор из ADMIN_IDS; ключ можно отозвать в «Вход и почта»."""
import base64
import hashlib
import hmac
import json
import secrets
import struct
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

import db

CHALLENGE_TTL = 300        # секунд на то, чтобы приложить палец или показать лицо
MAX_CHALLENGES = 1000      # лимит ожидающих строк: нельзя забить память пустыми запросами
FLAG_UP, FLAG_UV, FLAG_AT = 0x01, 0x04, 0x40


class PasskeyError(Exception):
    """Ключ или подпись не прошли проверку (текст — для журнала, клиенту его не показываем)."""


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64u(text) -> bytes:
    try:
        return base64.urlsafe_b64decode(str(text) + "=" * (-len(str(text)) % 4))
    except (ValueError, TypeError):
        raise PasskeyError("не base64url") from None


# ---------- CBOR (то, что присылает телефон) ----------

def cbor_decode(data: bytes, pos: int = 0, depth: int = 0) -> tuple[object, int]:
    """Минимальный разбор CBOR: числа, байты, текст, массивы, словари, true/false/null; без бесконечных длин."""
    if depth > 8:
        raise PasskeyError("CBOR: слишком глубоко")
    try:
        first = data[pos]
        major, info = first >> 5, first & 31
        pos += 1
        if info < 24:
            n = info
        elif info == 24:
            n, pos = data[pos], pos + 1
        elif info == 25:
            n, pos = struct.unpack(">H", data[pos:pos + 2])[0], pos + 2
        elif info == 26:
            n, pos = struct.unpack(">I", data[pos:pos + 4])[0], pos + 4
        else:
            raise PasskeyError("CBOR: длина не поддерживается")
        if major == 0:
            return n, pos
        if major == 1:
            return -1 - n, pos
        if major in (2, 3):
            chunk = data[pos:pos + n]
            if len(chunk) != n:
                raise PasskeyError("CBOR: обрыв")
            return (bytes(chunk) if major == 2 else chunk.decode("utf-8")), pos + n
        if major == 4:
            out = []
            for _ in range(n):
                item, pos = cbor_decode(data, pos, depth + 1)
                out.append(item)
            return out, pos
        if major == 5:
            out = {}
            for _ in range(n):
                k, pos = cbor_decode(data, pos, depth + 1)
                v, pos = cbor_decode(data, pos, depth + 1)
                if isinstance(k, (list, dict)):
                    raise PasskeyError("CBOR: странный ключ")
                out[k] = v
            return out, pos
        if major == 7 and info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], pos
    except (IndexError, struct.error, UnicodeDecodeError):
        raise PasskeyError("CBOR: обрыв") from None
    raise PasskeyError("CBOR: тип не поддерживается")


# ---------- проверка ----------

def parse_auth_data(auth: bytes) -> dict:
    """authenticatorData: хеш домена, флаги, счётчик и (при создании ключа) сам ключ."""
    if len(auth) < 37:
        raise PasskeyError("authData короткая")
    out = {"rp_hash": auth[:32], "flags": auth[32], "counter": struct.unpack(">I", auth[33:37])[0], "cred_id": None, "cose": None}
    if out["flags"] & FLAG_AT:
        if len(auth) < 55:
            raise PasskeyError("нет данных ключа")
        n = struct.unpack(">H", auth[53:55])[0]
        out["cred_id"] = bytes(auth[55:55 + n])
        if len(out["cred_id"]) != n or n == 0 or n > 1023:
            raise PasskeyError("id ключа")
        out["cose"], _ = cbor_decode(auth, 55 + n)
    return out


def _client_data(raw: bytes, kind: str, challenge_purpose: str, uid: int | None, origins: set[str]) -> None:
    """clientDataJSON: тип операции, одноразовая строка, адрес сайта."""
    try:
        cd = json.loads(raw.decode("utf-8"))
        kind_got, challenge, origin = cd["type"], cd["challenge"], cd["origin"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise PasskeyError("clientData") from None
    if kind_got != kind:
        raise PasskeyError("не та операция")
    if not take_challenge(str(challenge), challenge_purpose, uid):  # строка гаснет в любом случае — повторить нельзя
        raise PasskeyError("строка не наша или устарела")
    if origin not in origins or cd.get("crossOrigin") is True:
        raise PasskeyError("чужой адрес сайта")


def _auth_flags(auth: dict, rp_id: str) -> None:
    if not hmac.compare_digest(auth["rp_hash"], hashlib.sha256(rp_id.encode()).digest()):
        raise PasskeyError("чужой домен")
    if not (auth["flags"] & FLAG_UP and auth["flags"] & FLAG_UV):
        raise PasskeyError("нет Face ID / отпечатка")


def verify_registration(client_data_json: bytes, attestation_object: bytes, *, uid: int, origins: set[str],
                        rp_id: str) -> tuple[bytes, bytes]:
    """Новый ключ → (id ключа, открытый ключ в виде 65 байт X9.62). Только ES256 / P-256."""
    _client_data(client_data_json, "webauthn.create", "reg", uid, origins)
    att, _ = cbor_decode(attestation_object)
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise PasskeyError("attestationObject")
    auth = parse_auth_data(att["authData"])
    _auth_flags(auth, rp_id)
    cose = auth["cose"]
    if auth["cred_id"] is None or not isinstance(cose, dict) or cose.get(1) != 2 or cose.get(3) != -7 or cose.get(-1) != 1:
        raise PasskeyError("нужен ключ ES256 / P-256")
    x, y = cose.get(-2), cose.get(-3)
    if not (isinstance(x, bytes) and isinstance(y, bytes) and len(x) == 32 and len(y) == 32):
        raise PasskeyError("координаты ключа")
    point = b"\x04" + x + y
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)  # точка действительно на кривой
    except ValueError:
        raise PasskeyError("ключ не на кривой") from None
    return auth["cred_id"], point


def verify_assertion(client_data_json: bytes, authenticator_data: bytes, signature: bytes, *, public_key: bytes,
                     stored_count: int, origins: set[str], rp_id: str) -> int:
    """Вход по ключу → новое значение счётчика. PasskeyError — подпись или условия не сошлись."""
    _client_data(client_data_json, "webauthn.get", "login", None, origins)
    auth = parse_auth_data(authenticator_data)
    _auth_flags(auth, rp_id)
    signed = authenticator_data + hashlib.sha256(client_data_json).digest()
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), public_key).verify(
            signature, signed, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError):
        raise PasskeyError("подпись не подошла") from None
    if (stored_count or auth["counter"]) and auth["counter"] <= stored_count:
        raise PasskeyError("счётчик пошёл назад: возможна копия ключа")
    return auth["counter"]


# ---------- одноразовые строки (challenge) ----------

_challenges: dict[str, tuple[float, str, int | None]] = {}


def new_challenge(purpose: str, uid: int | None = None) -> str:
    now = time.time()
    for k in [k for k, (t, _, _) in _challenges.items() if now - t > CHALLENGE_TTL]:
        _challenges.pop(k, None)
    while len(_challenges) >= MAX_CHALLENGES:
        _challenges.pop(next(iter(_challenges)))
    token = b64u(secrets.token_bytes(32))
    _challenges[token] = (now, purpose, uid)
    return token


def take_challenge(token: str, purpose: str, uid: int | None) -> bool:
    entry = _challenges.pop(token, None)
    return bool(entry) and time.time() - entry[0] <= CHALLENGE_TTL and entry[1] == purpose and entry[2] == uid


# ---------- хранилище ----------

async def add(uid: int, cred_id: bytes, public_key: bytes, device: str) -> bool:
    """False — такой ключ уже есть."""
    row = await db.fetch_one("SELECT 1 AS x FROM passkeys WHERE cred_id=?", (b64u(cred_id),))
    if row:
        return False
    await db.execute("INSERT INTO passkeys (uid, cred_id, public_key, device) VALUES (?,?,?,?)",
                     (uid, b64u(cred_id), b64u(public_key), device))
    return True


async def list_for(uid: int) -> list[dict]:
    return await db.fetch_all("SELECT id, cred_id, device, created_at, last_used FROM passkeys WHERE uid=? ORDER BY id", (uid,))


async def revoke(uid: int, key_id: int) -> None:
    await db.execute("DELETE FROM passkeys WHERE id=? AND uid=?", (key_id, uid))
