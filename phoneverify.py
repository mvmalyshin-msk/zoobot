"""Подтверждение телефона через Яндекс ID (аккаунт Яндекса, Телемост): клиент с сайта разрешает доступ к номеру,
Яндекс отдаёт номер из своего профиля. Бесплатно, но нужно зарегистрировать приложение на oauth.yandex.ru
с правом «Номер телефона» и задать YANDEX_CLIENT_ID и YANDEX_CLIENT_SECRET в Railway → Variables.
Без этих переменных кнопка не показывается, а маршруты отвечают 404."""
import logging
import secrets
import time
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
_states: dict[str, tuple[int, float]] = {}  # state -> (uid гостя, время истечения); живёт в памяти процесса


def enabled() -> bool:
    return bool(config.yandex_client_id and config.yandex_client_secret and config.webapp_url)


def _redirect_uri() -> str:
    return f"{config.webapp_url}/verify/yandex/cb"


def _prune(now: float) -> None:
    for st in [s for s, (_, exp) in _states.items() if exp < now]:
        _states.pop(st, None)


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


async def start(request: web.Request) -> web.Response:
    if not enabled():
        return web.Response(status=404, text="Подтверждение через Яндекс пока не настроено.")
    uid = await webchat.session_uid(request)
    if uid is None:
        return web.Response(status=302, headers={"Location": "/chat"})
    now = time.time()
    _prune(now)
    state = secrets.token_urlsafe(24)
    _states[state] = (uid, now + STATE_SECONDS)
    url = AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": config.yandex_client_id,
                                      "redirect_uri": _redirect_uri(), "state": state, "force_confirm": "yes"})
    return web.Response(status=302, headers={"Location": url})


async def callback(request: web.Request) -> web.Response:
    code = request.query.get("code", "")
    saved = _states.pop(request.query.get("state", ""), None)
    uid = await webchat.session_uid(request)
    # номер привязываем только к тому гостю, который начал подтверждение в этом же браузере
    if not enabled() or not code or not saved or uid is None or saved[0] != uid or saved[1] < time.time():
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
    app.router.add_get("/verify/yandex", start)
    app.router.add_get("/verify/yandex/cb", callback)
