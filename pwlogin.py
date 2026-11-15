"""Вход клиента по почте и паролю — без писем и кодов (почта на хостинге без платного домена не доходит).

Пароль хранится только как соль + scrypt-хеш. Почта для такого входа лежит в отдельном столбце login_email:
она никем не подтверждена, поэтому в users.email (подтверждённая почта) не попадает и к чужим данным не привязывается.
Забытый пароль восстановить нельзя (письма нет): клиент входит через Telegram или оставляет заявку.
"""
import hashlib
import hmac
import os
import re
import secrets

from aiohttp import web

import db

MIN_LEN = 8
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_N, _R, _P = 2 ** 14, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=_N, r=_R, p=_P, dklen=32)
        return hmac.compare_digest(got.hex(), h)
    except (ValueError, TypeError):
        return False


FORM = ("<div class=auth-sep>или по почте и паролю</div>"
        "<form method=post action=\"/chat/login/pw\">"
        "<input name=\"email\" type=\"email\" inputmode=email autocomplete=username placeholder='Ваша почта' aria-label='Почта' required>"
        "<input name=\"password\" type=\"password\" autocomplete=current-password minlength=8 placeholder='Пароль (не короче 8 знаков)' "
        "aria-label='Пароль' required>"
        "<button class='auth-btn main' name=mode value=login>Войти</button>"
        "<button class='auth-btn' name=mode value=register>Зарегистрироваться</button></form>"
        "<p class=auth-note>Письма не отправляются. Если забудете пароль, войдите через Telegram или оставьте заявку.</p>")


def _form_page(msg: str) -> web.Response:
    import webchat
    return webchat._phone_form(msg)


async def post_login(request: web.Request) -> web.Response:
    import portal
    import webchat
    if not portal._same_origin(request):
        return web.Response(status=403, text="Нет доступа")
    form = await request.post()
    mode = "register" if form.get("mode") == "register" else "login"
    email = str(form.get("email", "")).strip().lower()[:200]
    password = str(form.get("password", ""))[:200]
    if not _EMAIL_RE.match(email):
        return _form_page("Проверьте адрес почты: он должен выглядеть как name@example.com.")
    ip = webchat.client_ip(request)
    if portal._limit(portal._hits, "pw:" + ip, 20, 600) or portal._limit(portal._fails, "pw:" + email, 8, 600):
        return _form_page("Слишком много попыток. Подождите 10 минут.")
    device = webchat.device_label(request.headers.get("User-Agent"))
    if mode == "register":
        return await _register(request, email, password, device, ip)
    row = await db.fetch_one("SELECT * FROM users WHERE login_email=? AND erased_at IS NULL", (email,))
    stored = row["pw_hash"] if row else None
    ok = verify_password(password, stored or "scrypt$00$00")   # время ответа одинаково для чужой почты и неверного пароля
    if not row or not stored or not ok:
        return _form_page("Неверная почта или пароль.")
    token = await webchat.new_session(row["tg_id"], device=device, ip=ip)
    resp = web.Response(status=302, headers={"Location": "/chat"})
    webchat._set_cookie(request, resp, token)
    return resp


async def _register(request: web.Request, email: str, password: str, device: str, ip: str) -> web.Response:
    import portal
    import webchat
    if len(password) < MIN_LEN:
        return _form_page(f"Пароль должен быть не короче {MIN_LEN} знаков.")
    if await webchat._is_admin_email(email) or email == (os.getenv("ADMIN_EMAIL") or "").strip().lower():
        return _form_page(webchat.ADMIN_AS_CLIENT_TEXT)
    if portal._limit(portal._hits, "pwreg:" + ip, 10, 3600):
        return _form_page("Слишком много регистраций с вашего адреса. Попробуйте позже.")
    if await db.fetch_one("SELECT 1 AS x FROM users WHERE login_email=?", (email,)):
        return _form_page("Эта почта уже зарегистрирована. Нажмите «Войти».")
    token = await webchat.new_session(None, device=device, ip=ip)
    uid = (await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(token),)))["uid"]
    await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (uid,))
    await db.execute("UPDATE users SET login_email=?, pw_hash=?, updated_at=datetime('now') WHERE tg_id=?",
                     (email, hash_password(password), uid))
    resp = web.Response(status=302, headers={"Location": "/chat"})
    webchat._set_cookie(request, resp, token)
    return resp
