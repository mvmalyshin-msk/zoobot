"""Подтверждение телефона через Яндекс ID (аккаунт Яндекса, Телемост): клиент с сайта разрешает доступ к номеру,
Яндекс отдаёт номер из своего профиля. Бесплатно, но нужно зарегистрировать приложение на oauth.yandex.ru
с правом «Номер телефона» и задать YANDEX_CLIENT_ID и YANDEX_CLIENT_SECRET в переменных проекта.
Без этих переменных кнопка не показывается, а маршруты отвечают 404."""
import logging
import secrets
from urllib.parse import urlencode

from aiohttp import ClientSession, web

import db
import orders
import texts
import webchat
from config import config
from utils import normalize_phone

log = logging.getLogger(__name__)

AUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info"
STATE_SECONDS = 600
# Состояния (state) хранятся в базе (loginstore): переживают перезапуск, пока человек подтверждает вход в Яндексе.
# 'yv' — подтверждение номера гостем чата (ключ — state, данные — его uid), 'yl' — вход на странице входа.


def enabled() -> bool:
    return bool(config.yandex_client_id and config.yandex_client_secret and config.webapp_url)


def _redirect_uri() -> str:
    return f"{config.webapp_url}/verify/yandex/cb"


async def exchange_code(code: str) -> str:
    """Меняет одноразовый код на токен доступа. Ошибка Яндекса — исключение."""
    async with ClientSession() as s:
        async with s.post(TOKEN_URL, timeout=10, data={
                "grant_type": "authorization_code", "code": code,
                "client_id": config.yandex_client_id, "client_secret": config.yandex_client_secret}) as r:
            body = await r.json(content_type=None)
    return body["access_token"]


async def fetch_phone(token: str) -> str | None:
    """Номер из профиля Яндекс ID (поле default_phone.number); без права доступа его нет."""
    async with ClientSession() as s:
        async with s.get(INFO_URL, params={"format": "json"}, timeout=10,
                         headers={"Authorization": f"OAuth {token}"}) as r:
            body = await r.json(content_type=None)
    return (body.get("default_phone") or {}).get("number")


async def fetch_email(token: str) -> str | None:
    """Почта из профиля Яндекс ID (default_email): вход, если номера в Яндексе нет или доступ к нему не дан."""
    async with ClientSession() as s:
        async with s.get(INFO_URL, params={"format": "json"}, timeout=10,
                         headers={"Authorization": f"OAuth {token}"}) as r:
            body = await r.json(content_type=None)
    mail = str(body.get("default_email") or "").strip().lower()
    return mail if "@" in mail else None


async def start(request: web.Request) -> web.Response:
    if not enabled():
        return web.Response(status=404, text="Подтверждение через Яндекс пока не настроено.")
    uid = await webchat.session_uid(request)
    if uid is None:
        return web.Response(status=302, headers={"Location": "/chat"})
    import loginstore
    state = secrets.token_urlsafe(24)
    await loginstore.put("yv", state, {"uid": uid}, STATE_SECONDS)
    url = AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": config.yandex_client_id,
                                      "redirect_uri": _redirect_uri(), "state": state, "force_confirm": "yes"})
    return web.Response(status=302, headers={"Location": url})


async def start_login(request: web.Request) -> web.Response:
    """Вход по номеру через Яндекс ID: кабинет открывается по номеру из профиля Яндекса — без кодов, СМС и VPN."""
    if not enabled():
        return web.Response(status=404, text="Вход через Яндекс пока не настроен.")
    import loginstore
    state = secrets.token_urlsafe(24)
    await loginstore.put("yl", state, {}, STATE_SECONDS)
    url = AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": config.yandex_client_id,
                                      "redirect_uri": _redirect_uri(), "state": state, "force_confirm": "yes"})
    return web.Response(status=302, headers={"Location": url})


def _fail(text: str) -> web.Response:
    return web.Response(status=403, text=text)


async def _login_callback(request: web.Request, code: str) -> web.Response:
    phone = email = None
    try:
        token = await exchange_code(code)
        phone = normalize_phone(await fetch_phone(token) or "")
        if not phone:                                  # номера нет — входим по почте из Яндекса
            email = (await fetch_email(token) or "").strip().lower() or None
    except Exception:  # сеть или отказ Яндекса
        log.exception("Яндекс ID: не удалось получить номер или почту для входа")
    if not phone and not email:
        return _fail("Яндекс не передал ни номер, ни почту. Вернитесь на страницу входа и выберите «Войти через Telegram».")
    if phone:
        user = await db.fetch_one(
            "SELECT * FROM users WHERE phone=? AND erased_at IS NULL ORDER BY (tg_id>0) DESC, id LIMIT 1", (phone,))
    else:
        user = await db.fetch_one(
            "SELECT * FROM users WHERE email=? AND erased_at IS NULL ORDER BY (tg_id>0) DESC, id LIMIT 1", (email,))
    if user and user["tg_id"] in webchat.config.admin_ids:  # настоящий конфиг: админ — один
        return _fail(webchat.ADMIN_AS_CLIENT_TEXT)
    device = webchat.device_label(request.headers.get("User-Agent"))
    ip = webchat.client_ip(request)
    if user:
        uid = user["tg_id"]
        token = await webchat.new_session(uid, device=device, ip=ip)
        if phone:
            await db.execute("UPDATE users SET phone_verified='yandex', updated_at=datetime('now') WHERE id=? "
                             "AND (phone_verified IS NULL OR phone_verified<>'telegram')", (user["id"],))
    else:   # новый клиент: заводим гостя с подтверждённым номером (или почтой из Яндекса)
        token = await webchat.new_session(None, device=device, ip=ip)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(token),)))["uid"]
        await db.execute("INSERT OR IGNORE INTO users (tg_id) VALUES (?)", (uid,))
        if phone:
            await db.execute("UPDATE users SET phone=?, phone_verified='yandex', updated_at=datetime('now') WHERE tg_id=?",
                             (phone, uid))
        else:   # почту подтвердил Яндекс; «подтверждённый» вход для кабинета — как у входа по коду из письма
            await db.execute("UPDATE users SET email=?, phone_verified='yandex', updated_at=datetime('now') WHERE tg_id=?",
                             (email, uid))
    resp = web.Response(status=302, headers={"Location": "/chat"})
    webchat._set_cookie(request, resp, token)
    return resp


async def callback(request: web.Request) -> web.Response:
    import loginstore
    code = request.query.get("code", "")
    state = request.query.get("state", "")[:100]
    if state and await loginstore.take("yl", state) is not None:   # состояние выдано странице входа, а не гостю в чате
        if not enabled() or not code:
            return _fail("Вход не удался. Вернитесь на страницу входа и нажмите кнопку ещё раз.")
        return await _login_callback(request, code)
    saved = await loginstore.take("yv", state) if state else None
    uid = await webchat.session_uid(request)
    # номер привязываем только к тому гостю, который начал подтверждение в этом же браузере
    if not enabled() or not code or not saved or uid is None or saved["uid"] != uid:
        return web.Response(status=403, text="Подтверждение не удалось. Откройте чат и нажмите «Подтвердить через Яндекс» ещё раз.")
    phone = None
    try:
        phone = normalize_phone(await fetch_phone(await exchange_code(code)) or "")
    except Exception:  # сеть или отказ Яндекса: клиент просто попробует ещё раз
        log.exception("Яндекс ID: не удалось получить номер")
    if phone:
        await db.execute("UPDATE users SET phone=?, phone_verified='yandex', updated_at=datetime('now') WHERE tg_id=?",
                         (phone, uid))
        await orders.notify(request.app["bot"], uid, texts.PHONE_VERIFIED_YANDEX.format(phone=phone))
    else:
        await orders.notify(request.app["bot"], uid, texts.YANDEX_NO_PHONE)
    return web.Response(status=302, headers={"Location": "/chat"})


def setup(app: web.Application) -> None:
    app.router.add_get("/chat/login/yandex", start_login)
    app.router.add_get("/verify/yandex", start)
    app.router.add_get("/verify/yandex/cb", callback)
