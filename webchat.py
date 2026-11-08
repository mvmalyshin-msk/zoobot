"""Бот в браузере: та же логика, что в Telegram, по ссылке /chat (работает без Telegram и без VPN).

Как устроено: браузер шлёт нажатия и текст на /chat/api/send, мы собираем из них обычное обновление Telegram и
отдаём тому же диспетчеру, что и настоящие сообщения. Ответы бота перехватывает WebRouterSession: для веб-клиентов
(отрицательные id) и для сообщений, пришедших из браузера, она кладёт их в таблицу web_events вместо отправки в
Telegram; страница забирает события длинным опросом. Поэтому уведомления, напоминания и отчёты работают по тем же
каналам, что и в Telegram, а админ при входе по одноразовой ссылке (/webadmin) пользуется своим же админ-аккаунтом.
"""
import asyncio
import contextvars
import hashlib
import json
import math
import re
import logging
import secrets
import time
from datetime import datetime

from aiogram.client.session.base import BaseSession
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, EditMessageReplyMarkup, EditMessageText,
                             SendDocument, SendMessage, SendPhoto)
from aiogram.types import (Chat, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiohttp import web

import brand
import db
import ribbons
from config import config

log = logging.getLogger(__name__)

COOKIE = "zcw"        # клиент (и гость) на сайте
ADMIN_COOKIE = "zca"  # администратор: отдельная cookie, чтобы вход в портал не подменял клиента в соседней вкладке
ORIGIN: contextvars.ContextVar[int | None] = contextvars.ContextVar("web_origin", default=None)
# Чьё действие в Telegram сейчас обрабатывается. Ответы бота этому человеку — часть его разговора в Telegram,
# их на сайт не копируем; копируем то, что пришло ему со стороны (ответ админа, статус заказа, напоминание).
TG_ORIGIN: contextvars.ContextVar[int | None] = contextvars.ContextVar("tg_origin", default=None)


async def tg_origin_middleware(handler, event, data):
    """Внешний middleware диспетчера: помечает обновления из Telegram (из браузера помечает feed через ORIGIN)."""
    user = data.get("event_from_user")
    if ORIGIN.get() is not None or user is None:
        return await handler(event, data)
    token = TG_ORIGIN.set(user.id)
    try:
        return await handler(event, data)
    finally:
        TG_ORIGIN.reset(token)
KEEP_EVENTS = 1500
POLL_SECONDS = 20
SEND_LIMIT = 60          # действий в минуту с одной сессии
_hits: dict[int, list[float]] = {}
_locks: dict[int, asyncio.Lock] = {}
_linked_cache: dict[int, tuple[float, bool]] = {}
_tg_cache: dict[int, tuple[float, int | None]] = {}
_targets_cache: dict[int, tuple[float, list[int]]] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------- Что показываем в браузере ----------

def serialize_markup(markup) -> dict:
    """Клавиатуру Telegram → простой JSON. Кнопки, которых в браузере нет (карта, геопозиция), пропускаем."""
    if isinstance(markup, InlineKeyboardMarkup):
        rows = []
        for row in markup.inline_keyboard:
            out = []
            for b in row:
                if b.callback_data:
                    out.append({"t": b.text, "cb": b.callback_data})
                elif b.url:
                    out.append({"t": b.text, "url": b.url})
            if out:
                rows.append(out)
        return {"inline": rows}
    if isinstance(markup, ReplyKeyboardMarkup):
        rows = []
        for row in markup.keyboard:
            out = []
            for b in row:
                if getattr(b, "web_app", None):  # карта: в браузере открывается та же страница
                    out.append({"t": b.text, "map": b.web_app.url})
                elif getattr(b, "request_location", None):  # геопозиция: браузер спросит разрешение сам
                    out.append({"t": b.text, "loc": True})
                elif getattr(b, "request_contact", None):
                    out.append({"t": b.text, "contact": True})
                else:
                    out.append({"t": b.text})
            if out:
                rows.append(out)
        return {"reply": rows}
    if isinstance(markup, ReplyKeyboardRemove):
        return {"reply": None}
    return {}


async def push(uid: int, op: str, *, mid: int | None = None, **payload) -> int:
    """Кладёт событие в ленту пользователя. Для новых сообщений mid = id события."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            "INSERT INTO web_events (uid, op, mid, payload) VALUES (?,?,?,?)",
            (uid, op, mid, json.dumps(payload, ensure_ascii=False)))
        eid = cur.lastrowid
        if mid is None:
            await conn.execute("UPDATE web_events SET mid=? WHERE id=?", (eid, eid))
        await conn.execute("DELETE FROM web_events WHERE uid=? AND id<?", (uid, eid - KEEP_EVENTS))
    if op == "send" and payload.get("who") == "bot" and ORIGIN.get() != uid:
        # новое сообщение не в ответ на действие человека в этом браузере — уведомление (Web Push)
        text = payload.get("text") or ("📎 " + payload["file"]["name"] if payload.get("file") else "Новое сообщение")
        asyncio.create_task(_notify(uid, text))
    return eid


async def _notify(uid: int, text: str) -> None:
    import webpush
    try:
        await webpush.notify(uid, text)
    except Exception:
        log.exception("Web Push: не удалось отправить уведомление")


async def _is_linked_admin(uid: int) -> bool:
    """Админ, который сейчас вошёл в браузере: ему уведомления идут и туда тоже."""
    if uid not in config.admin_ids:
        return False
    cached = _linked_cache.get(uid)
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    row = await db.fetch_one("SELECT 1 AS x FROM web_sessions WHERE uid=? LIMIT 1", (uid,))
    _linked_cache[uid] = (time.time(), bool(row))
    return bool(row)


async def _verified_tg(uid: int) -> int | None:
    cached = _tg_cache.get(uid)
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    row = await db.fetch_one("SELECT verified_tg_id FROM users WHERE tg_id=?", (uid,))
    tg = row["verified_tg_id"] if row else None
    _tg_cache[uid] = (time.time(), tg)
    return tg


async def _web_targets(tg: int) -> list[int]:
    """Чаты на сайте, куда копируем сообщение Telegram-клиенту: его сессия сайта (вход по ссылке из бота)
    и гостевые сессии, подтвердившие этот Telegram. Пусто — клиент сайтом не пользуется."""
    cached = _targets_cache.get(tg)
    if cached and time.time() - cached[0] < 30:
        return cached[1]
    rows = await db.fetch_all(
        "SELECT DISTINCT uid FROM web_sessions WHERE uid=? "
        "UNION SELECT DISTINCT s.uid FROM web_sessions s JOIN users u ON u.tg_id = s.uid WHERE u.verified_tg_id=?",
        (tg, tg))
    targets = [r["uid"] for r in rows]
    _targets_cache[tg] = (time.time(), targets)
    return targets


async def make_tg_link(web_uid: int) -> str:
    """Одноразовая ссылка (сутки): клиент с сайта открывает её в Telegram — и мы знаем, что он настоящий."""
    token = secrets.token_urlsafe(24)
    await db.execute("INSERT INTO web_links (token_hash, web_uid, expires_at) VALUES (?, ?, datetime('now', '+1 day'))",
                     (_hash(token), web_uid))
    return f"https://t.me/{config.bot_username}?start=wl_{token}"


async def claim_tg_link(token: str, tg_id: int) -> int | None:
    """Привязывает веб-клиента к Telegram. Возвращает id веб-клиента или None (ссылка чужая, старая или уже использована)."""
    h = _hash(token)
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"UPDATE web_links SET used=1, tg_id=?, {db.touch()} WHERE token_hash=? AND used=0 AND expires_at > datetime('now')",
            (tg_id, h))
        if cur.rowcount != 1:
            return None
        cur = await conn.execute("SELECT web_uid FROM web_links WHERE token_hash=?", (h,))
        web_uid = (await cur.fetchone())[0]
        await conn.execute(f"UPDATE users SET verified_tg_id=?, {db.touch()} WHERE tg_id=?", (tg_id, web_uid))
    _tg_cache.pop(web_uid, None)
    _targets_cache.clear()
    return web_uid


class WebRouterSession(BaseSession):
    """Обычная сессия aiogram, которая сообщения для браузера кладёт в web_events, а остальное шлёт в Telegram."""

    def __init__(self, inner: BaseSession):
        super().__init__()
        self.inner = inner

    async def close(self):
        await self.inner.close()

    async def stream_content(self, *a, **kw):
        async for chunk in self.inner.stream_content(*a, **kw):
            yield chunk

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, AnswerCallbackQuery) and str(method.callback_query_id).startswith("web:"):
            return True
        cid = getattr(method, "chat_id", None)
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if cid is None:
            return await self.inner.make_request(bot, method, timeout)
        origin = ORIGIN.get()
        sending = isinstance(method, (SendMessage, SendPhoto, SendDocument))
        if cid < 0 or origin == cid:
            result = await self._web(cid, method)
            if cid < 0 and origin != cid and sending:  # уведомление клиенту с сайта, подтвердившему Telegram
                tg = await _verified_tg(cid)
                if tg and tg != TG_ORIGIN.get():  # тот, кто сейчас действует в Telegram, уже видит ответ там
                    copy = SendMessage(chat_id=tg, text=getattr(method, "text", None) or getattr(method, "caption", None)
                                       or "Новое сообщение в чате на сайте")
                    asyncio.create_task(self._telegram_quietly(bot, copy, timeout))
            return result
        if sending and await _is_linked_admin(cid):  # уведомление админу: и в Telegram, и в браузер
            result = await self._web(cid, method)
            asyncio.create_task(self._telegram_quietly(bot, method, timeout))
            return result
        if sending and cid > 0 and TG_ORIGIN.get() != cid:
            targets = await _web_targets(cid)
            if targets:  # клиент пользуется чатом на сайте: ответ админа и статусы — и туда, и в Telegram
                copy = None
                for uid in targets:
                    try:
                        copy = await self._web(uid, method)
                    except Exception:
                        log.exception("Не удалось положить копию сообщения в чат на сайте")
                try:
                    return await self.inner.make_request(bot, method, timeout)
                except Exception as e:
                    if copy is None:
                        raise
                    log.info("Telegram не принял сообщение для %s (%s): оно есть в чате на сайте", cid, type(e).__name__)
                    return copy
        return await self.inner.make_request(bot, method, timeout)

    async def _telegram_quietly(self, bot, method, timeout):
        try:
            await self.inner.make_request(bot, method, timeout)
        except Exception as e:  # Telegram может быть недоступен — в браузере сообщение уже есть
            log.info("Telegram недоступен для дубля уведомления: %s", type(e).__name__)

    async def _web(self, cid: int, method):
        chat = Chat(id=cid, type="private")
        if isinstance(method, SendMessage):
            eid = await push(cid, "send", who="bot", text=method.text, **serialize_markup(method.reply_markup))
            return Message(message_id=eid, date=datetime.now(), chat=chat, text=method.text)
        if isinstance(method, SendDocument):
            f = method.document
            data = getattr(f, "data", None)
            file_id = None
            if data:
                async with db.transaction() as conn:
                    cur = await conn.execute("INSERT INTO web_files (uid, name, data) VALUES (?,?,?)",
                                             (cid, getattr(f, "filename", None) or "file", data))
                    file_id = cur.lastrowid
            eid = await push(cid, "send", who="bot", text=method.caption or "",
                             file={"id": file_id, "name": getattr(f, "filename", None) or "file"} if file_id else None,
                             **serialize_markup(method.reply_markup))
            return Message(message_id=eid, date=datetime.now(), chat=chat)
        if isinstance(method, SendPhoto):
            eid = await push(cid, "send", who="bot", text=(method.caption or "") + "\n📷 Фото доступно в Telegram",
                             **serialize_markup(method.reply_markup))
            return Message(message_id=eid, date=datetime.now(), chat=chat)
        if isinstance(method, EditMessageText):
            await push(cid, "edit", mid=method.message_id, text=method.text, **serialize_markup(method.reply_markup))
            return True
        if isinstance(method, EditMessageReplyMarkup):
            await push(cid, "edit", mid=method.message_id, **(serialize_markup(method.reply_markup) or {"inline": []}))
            return True
        if isinstance(method, DeleteMessage):
            await push(cid, "delete", mid=method.message_id)
            return True
        return True  # «печатает…» и прочее в браузере не нужно


# ---------- Сессии и вход ----------

def device_label(ua: str | None) -> str:
    """Короткое название устройства по User-Agent для списка доверенных устройств («iPhone · Safari»). Только из известных слов."""
    ua = ua or ""
    for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("Macintosh", "Mac"),
                      ("Windows", "Windows"), ("Linux", "Linux")):
        if key in ua:
            device = name
            break
    else:
        return "Неизвестное устройство"
    for key, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("YaBrowser", "Яндекс Браузер"), ("CriOS", "Chrome"),
                      ("FxiOS", "Firefox"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari")):
        if key in ua:
            return f"{device} · {name}"
    return device


def client_ip(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote or "?")[:64]


async def new_session(uid: int | None = None, device: str | None = None, ip: str | None = None) -> str:
    """Выдаёт токен. Без uid — гость: отрицательный id, не пересекается с настоящими Telegram-id."""
    token = secrets.token_urlsafe(32)
    async with db.transaction() as conn:
        cur = await conn.execute("INSERT INTO web_sessions (token_hash, uid, device, ip, seen_at) VALUES (?, ?, ?, ?, datetime('now'))",
                                 (_hash(token), uid if uid is not None else 0, device, ip))
        if uid is None:
            await conn.execute("UPDATE web_sessions SET uid=? WHERE id=?", (-cur.lastrowid, cur.lastrowid))
    _linked_cache.clear()
    _targets_cache.clear()
    return token


async def session_uid(request: web.Request, admin: bool = False) -> int | None:
    """Чья это сессия. Клиент и админ живут на разных cookie и никогда не смешиваются:
    клиентская сессия не бывает админской (даже старая), админская — только у администратора."""
    token = request.cookies.get(ADMIN_COOKIE if admin else COOKIE)
    if not token:
        return None
    row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (_hash(token),))
    if not row:
        return None
    is_admin = row["uid"] in config.admin_ids
    return row["uid"] if is_admin == admin else None


def _set_cookie(request: web.Request, resp: web.StreamResponse, token: str, admin: bool = False) -> None:
    secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
    resp.set_cookie(ADMIN_COOKIE if admin else COOKIE, token, max_age=60 * 60 * 24 * 90, httponly=True, secure=secure, samesite="Lax", path="/")


async def make_login_link(tg_id: int, nxt: str = "chat") -> str:
    """Одноразовая ссылка для админа на 10 минут; вход подтверждается кнопкой, чтобы предпросмотр ссылки её не сжёг."""
    token = secrets.token_urlsafe(24)
    await db.execute("INSERT INTO web_login (token_hash, tg_id, expires_at) VALUES (?, ?, datetime('now', '+10 minutes'))",
                     (_hash(token), tg_id))
    return f"{config.webapp_url}/chat/login?t={token}" + ("&n=portal" if nxt == "portal" else "")


async def site_link_for(tg_id: int) -> str | None:
    """Ссылка «Открыть на сайте» для клиента из Telegram; без адреса сайта — None."""
    if not config.webapp_url:
        return None
    return await make_login_link(tg_id)


def _ui(name: str) -> str:
    from pathlib import Path
    return (Path(__file__).parent / "webapp" / name).read_text(encoding="utf-8")


def _chat_html(admin: bool) -> str:
    html = (_ui("chat.html").replace("__TG_URL__", f"https://t.me/{config.bot_username}")
            .replace("__RIBBON__", ribbons.html(ribbons.ADMIN if admin else ribbons.CLIENT))
            .replace("__BACK__", brand.back("/portal" if admin else "/", compact=True))
            .replace("__API__", "/portal/bot" if admin else "/chat")
            .replace("__LOGOUT__", "/portal/logout" if admin else "/chat/logout"))
    if admin:  # админский и клиентский интерфейсы не смешиваем: «Кабинет» — клиентский
        html = html.replace('<a href="/cabinet">Кабинет</a>', '<a href="/">🏠 Главная страница</a><a href="/portal">Заявки</a>')
        # на экране «Домой» — приложение администратора (открывает портал), а не клиентский чат
        html = (html.replace('href="/manifest.webmanifest"', 'href="/manifest-admin.webmanifest"')
                .replace('apple-mobile-web-app-title" content="Зооконсьерж"', 'apple-mobile-web-app-title" content="ЗК Админ"')
                .replace('application-name" content="Зооконсьерж"', 'application-name" content="ЗК Админ"'))
    return html


def _html_page(html: str) -> web.Response:
    return web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-cache", "Referrer-Policy": "no-referrer"})


async def page_chat(request: web.Request) -> web.Response:
    """Чат на сайте — только клиентский. Админ пишет боту из портала: /portal/bot."""
    uid = await session_uid(request)
    resp = _html_page(_chat_html(admin=False))
    if uid is None:  # нет клиентской сессии (или в ней старый админ) — новый гость
        _set_cookie(request, resp, await new_session(device=device_label(request.headers.get("User-Agent")), ip=client_ip(request)))
    else:  # запоминаем, с какого IP человек пришёл в этот раз
        await db.execute("UPDATE web_sessions SET ip=?, seen_at=datetime('now') WHERE token_hash=?",
                         (client_ip(request), _hash(request.cookies.get(COOKIE, ""))))
    return resp


async def page_admin_chat(request: web.Request) -> web.Response:
    """Чат администратора с ботом на сайте: внутри портала, на админской cookie, с красной лентой."""
    if await session_uid(request, admin=True) is None:
        return web.Response(status=303, headers={"Location": "/portal"})
    return _html_page(_chat_html(admin=True))


_login_codes: dict[str, dict] = {}   # телефон -> {h, uid, to, exp, tries, sent}; живёт в памяти, код действует 10 минут
CODE_TTL, CODE_COOLDOWN, CODE_TRIES = 600, 60, 5


def _login_shell(inner: str) -> web.Response:
    import brand
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{brand.NAME} — вход</title>{brand.HEAD}<body>{ribbons.html(ribbons.CLIENT)}"
            "<div style='max-width:420px;margin:6vh auto;padding:16px'>"
            f"{brand.backrow('/')}<div style='text-align:center;font-size:26px'>{brand.LOGO}</div>{inner}</div></body>")
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _phone_form(msg: str = "") -> web.Response:
    note = f"<p style='color:#b3261e'>{msg}</p>" if msg else ""
    return _login_shell(
        "<h2 style='text-align:center'>Вход для клиентов</h2>"
        "<p>Введите номер телефона, который вы указывали у нас. Мы пришлём код в Telegram — и откроем ваши заказы и данные.</p>"
        f"{note}<form method=post action=\"/chat/login/phone\"><label>Телефон</label>"
        "<input name=\"phone\" type=tel inputmode=tel autocomplete=tel placeholder='+7 900 000-00-00' required style='width:100%'>"
        "<button class='btn' style='width:100%;margin-top:12px'>Получить код</button></form>"
        "<p class=muted style='margin-top:18px'>Нет Telegram или вы у нас впервые? "
        "<a href=\"/request\">Оставьте заявку без входа</a> — администратор свяжется с вами.</p>")


def _code_form(phone: str, msg: str = "") -> web.Response:
    import html as _h
    note = f"<p style='color:#b3261e'>{msg}</p>" if msg else ""
    return _login_shell(
        "<h2 style='text-align:center'>Введите код</h2>"
        "<p>Если этот номер есть у нас и привязан к Telegram, код уже там (в чате с ботом). Он действует 10 минут.</p>"
        f"{note}<form method=post action=\"/chat/login/code\"><input type=hidden name=phone value='{_h.escape(phone, quote=True)}'>"
        "<input name=\"code\" inputmode=numeric autocomplete=one-time-code maxlength=6 placeholder='6 цифр' required style='width:100%'>"
        "<button class='btn' style='width:100%;margin-top:12px'>Войти</button></form>"
        "<p class=muted style='margin-top:18px'><a href=\"/chat/login\">Другой номер</a></p>")


async def post_login_phone(request: web.Request) -> web.Response:
    import portal
    from utils import normalize_phone
    if not portal._same_origin(request):
        return web.Response(status=403, text="Нет доступа")
    phone = normalize_phone(str((await request.post()).get("phone", "")))
    if phone is None:
        return _phone_form("Проверьте номер: нужен российский, например +7 900 000-00-00.")
    now = time.time()
    old = _login_codes.get(phone)
    if old and now - old["sent"] < CODE_COOLDOWN:
        return _code_form(phone)                      # слишком часто: новый код не шлём, старый ещё действует
    user = await db.fetch_one(
        "SELECT * FROM users WHERE phone=? AND erased_at IS NULL ORDER BY (tg_id>0) DESC, id LIMIT 1", (phone,))
    to = None
    if user and user["tg_id"] not in config.admin_ids:
        to = user["tg_id"] if user["tg_id"] > 0 else user["verified_tg_id"]
    entry = {"h": "", "uid": None, "to": None, "exp": now + CODE_TTL, "tries": 0, "sent": now}
    if user and to and to not in config.admin_ids:
        import orders
        code = f"{secrets.randbelow(10**6):06d}"
        if await orders.notify(request.app["bot"], to, f"Код для входа на сайт Зооконсьерж: {code}\n"
                               "Никому его не сообщайте. Если это были не вы — просто проигнорируйте."):
            entry.update(h=_hash(f"{phone}:{code}"), uid=user["tg_id"], to=to)
    _login_codes[phone] = entry                       # для неизвестных номеров запись пустая: ответ тот же, угадать нечего
    return _code_form(phone)


async def post_login_code(request: web.Request) -> web.Response:
    import portal
    from utils import normalize_phone
    if not portal._same_origin(request):
        return web.Response(status=403, text="Нет доступа")
    form = await request.post()
    phone = normalize_phone(str(form.get("phone", ""))) or ""
    entry = _login_codes.get(phone)
    code = re.sub(r"\D", "", str(form.get("code", "")))
    if not entry or not entry["h"] or entry["exp"] < time.time() or entry["tries"] >= CODE_TRIES:
        return _code_form(phone, "Код не подошёл или устарел. Запросите новый.")
    entry["tries"] += 1
    if not secrets.compare_digest(entry["h"], _hash(f"{phone}:{code}")):
        return _code_form(phone, "Неверный код.")
    _login_codes.pop(phone, None)                     # код одноразовый
    token = await new_session(entry["uid"], device=device_label(request.headers.get("User-Agent")))
    resp = web.Response(status=302, headers={"Location": "/chat"})
    _set_cookie(request, resp, token)
    return resp


async def page_login(request: web.Request) -> web.Response:
    import html as _h
    if not request.query.get("t"):
        return _phone_form()
    t = _h.escape(request.query.get("t", ""), quote=True)
    n = "portal" if request.query.get("n") == "portal" else "chat"
    import brand
    title, text = (("Вход администратора", "Нажмите кнопку, чтобы войти под своим аккаунтом администратора.")
                   if n == "portal" else ("Ваш кабинет", "Нажмите кнопку, чтобы открыть кабинет Зооконсьерж на этом телефоне."))
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{brand.NAME} — вход</title>{brand.HEAD_ADMIN if n == 'portal' else brand.HEAD}<body>{ribbons.html(ribbons.ADMIN if n == 'portal' else ribbons.CLIENT)}"
            "<div style='max-width:420px;margin:6vh auto;padding:16px'>"
            f"{brand.backrow('/')}<div style='text-align:center;font-size:26px'>{brand.LOGO}</div>"
            f"<h2 style='text-align:center'>{title}</h2><p>{text}</p>"
            f"<form method=post action=/chat/login><input type=hidden name=t value='{t}'><input type=hidden name=n value='{n}'>"
            "<button class='btn' style='width:100%'>Войти</button></form></div></body>")
    return web.Response(text=page, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


async def post_login(request: web.Request) -> web.Response:
    form = await request.post()
    h = _hash(str(form.get("t", "")))
    row = await db.fetch_one(
        "SELECT * FROM web_login WHERE token_hash=? AND used=0 AND expires_at > datetime('now')", (h,))
    tg = row["tg_id"] if row else None
    # по ссылке из Telegram заходит и клиент (в свой кабинет); портал — только администратор
    if not row or tg is None or tg <= 0 or (form.get("n") == "portal" and tg not in config.admin_ids):
        return web.Response(status=403, text="Ссылка не действует. Нажмите кнопку в Telegram ещё раз.")
    await db.execute(f"UPDATE web_login SET used=1, {db.touch()} WHERE token_hash=?", (h,))
    token = await new_session(row["tg_id"], device=device_label(request.headers.get("User-Agent")))
    admin = tg in config.admin_ids  # админ всегда входит на админскую cookie, клиент — на клиентскую
    where = "/portal" if form.get("n") == "portal" else ("/portal/bot" if admin else "/chat")
    resp = web.Response(status=302, headers={"Location": where})
    _set_cookie(request, resp, token, admin=admin)
    return resp


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE)
    if token:
        await db.execute("DELETE FROM web_sessions WHERE token_hash=?", (_hash(token),))
        _linked_cache.clear()
        _targets_cache.clear()
    resp = web.Response(status=302, headers={"Location": "/chat"})
    resp.del_cookie(COOKIE, path="/")
    return resp


# ---------- API страницы ----------

def _need_uid(handler, admin: bool = False):
    """API чата: клиентское (/chat/api) читает клиентскую cookie, админское (/portal/bot/api) — админскую."""
    async def wrapper(request: web.Request):
        uid = await session_uid(request, admin=admin)
        if uid is None:
            return web.json_response({"error": "session"}, status=401)
        if request.method == "POST" and request.headers.get("X-Zc") != "1":
            return web.json_response({"error": "csrf"}, status=403)
        return await handler(request, uid)
    wrapper.raw = handler
    return wrapper


def _row(r) -> dict:
    return {"id": r["id"], "op": r["op"], "mid": r["mid"], **json.loads(r["payload"])}


@_need_uid
async def api_history(request: web.Request, uid: int) -> web.Response:
    rows = await db.fetch_all("SELECT * FROM web_events WHERE uid=? ORDER BY id DESC LIMIT 400", (uid,))
    msgs: dict[int, dict] = {}
    for r in reversed(rows):
        e = _row(r)
        if e["op"] == "send":
            msgs[e["mid"]] = {k: v for k, v in e.items() if k not in ("op", "id")}
        elif e["op"] == "edit" and e["mid"] in msgs:
            msgs[e["mid"]].update({k: v for k, v in e.items() if k not in ("op", "id", "mid")})
        elif e["op"] == "delete":
            msgs.pop(e["mid"], None)
    last = rows[0]["id"] if rows else 0
    return web.json_response({"messages": list(msgs.values()), "last": last, "admin": uid in config.admin_ids})


@_need_uid
async def api_poll(request: web.Request, uid: int) -> web.Response:
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    deadline = time.time() + POLL_SECONDS
    while True:
        rows = await db.fetch_all("SELECT * FROM web_events WHERE uid=? AND id>? ORDER BY id LIMIT 100", (uid, after))
        if rows or time.time() > deadline:
            return web.json_response({"events": [_row(r) for r in rows]})
        await asyncio.sleep(0.7)


def _rate_ok(uid: int) -> bool:
    now = time.time()
    hits = [t for t in _hits.get(uid, []) if now - t < 60]
    ok = len(hits) < SEND_LIMIT
    if ok:
        hits.append(now)
    _hits[uid] = hits
    return ok


@_need_uid
async def api_send(request: web.Request, uid: int) -> web.Response:
    if not _rate_ok(uid):
        return web.json_response({"error": "rate"}, status=429)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "json"}, status=400)
    text = str(body.get("text") or "")[:1000]
    cb = str(body.get("cb") or "")[:64]
    phone = str(body.get("phone") or "")[:30]
    pick = str(body.get("pick") or "")[:500]
    loc = _parse_loc(body.get("loc"))
    if body.get("loc") is not None and loc is None:
        return web.json_response({"error": "loc"}, status=400)
    if not (text.strip() or cb or phone.strip() or pick or loc):
        return web.json_response({"error": "empty"}, status=400)
    lock = _locks.setdefault(uid, asyncio.Lock())
    async with lock:
        await feed(request.app, uid, text=text, cb=cb, mid=body.get("mid"), phone=phone, pick=pick, loc=loc)
    return web.json_response({"ok": True})


def _parse_loc(raw) -> dict | None:
    """Геопозиция из браузера: конечные координаты в допустимых пределах, иначе None."""
    if not isinstance(raw, dict):
        return None
    try:
        lat, lon = float(raw["lat"]), float(raw["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return {"lat": lat, "lon": lon}


async def feed(app, uid: int, *, text: str = "", cb: str = "", mid=None, phone: str = "",
               pick: str = "", loc: dict | None = None) -> None:
    """Превращает действие из браузера в обновление Telegram и отдаёт диспетчеру."""
    bot, dp = app["bot"], app["dp"]
    now = int(time.time())
    user = {"id": uid, "is_bot": False,
            "first_name": "Гость" if uid < 0 else ("Администратор" if uid in config.admin_ids else "Клиент")}
    chat = {"id": uid, "type": "private"}
    upd_id = int(time.time() * 1000) % 2_000_000_000
    if cb:
        src = await db.fetch_one("SELECT payload FROM web_events WHERE uid=? AND mid=? AND op='send'", (uid, int(mid or 0)))
        shown = json.loads(src["payload"]).get("text", "") if src else ""
        update = {"update_id": upd_id, "callback_query": {
            "id": f"web:{upd_id}", "from": user, "chat_instance": "web", "data": cb,
            "message": {"message_id": int(mid or 0), "date": now, "chat": chat, "text": shown or "·"}}}
    else:
        msg = {"message_id": upd_id, "date": now, "chat": chat, "from": user}
        if pick:  # адрес выбран на карте: тот же ответ, что приходит из мини-приложения в Telegram
            msg["web_app_data"] = {"data": pick, "button_text": "Карта"}
            shown = "🗺 Адрес выбран на карте"
        elif loc:
            msg["location"] = {"latitude": loc["lat"], "longitude": loc["lon"]}
            shown = "📍 Геопозиция отправлена"
        elif phone.strip():
            # user_id не указываем: телефон из браузера не подтверждён Telegram, профиль по нему не раскрываем
            msg["contact"] = {"phone_number": phone.strip(), "first_name": "Гость"}
            shown = "📱 " + phone.strip()
        else:
            msg["text"] = text
            shown = text
            if text.startswith("/"):
                msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
        await push(uid, "send", who="me", text=shown)
        update = {"update_id": upd_id, "message": msg}
    token = ORIGIN.set(uid)
    try:
        await dp.feed_raw_update(bot, update)
    except Exception:
        log.exception("Ошибка обработки действия из браузера")
    finally:
        ORIGIN.reset(token)


@_need_uid
async def api_file(request: web.Request, uid: int) -> web.Response:
    fid = request.match_info["fid"]
    row = await db.fetch_one("SELECT name, data FROM web_files WHERE id=? AND uid=?", (fid, uid)) if fid.isdigit() else None
    if not row:
        return web.Response(status=404)
    from urllib.parse import quote
    return web.Response(body=row["data"], content_type="application/octet-stream",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row['name'])}"})


async def api_push_key(request: web.Request, uid: int) -> web.Response:
    import webpush
    return web.json_response({"key": await webpush.public_key_b64()})


async def api_push_subscribe(request: web.Request, uid: int) -> web.Response:
    import webpush
    try:
        sub = await request.json()
    except (ValueError, UnicodeDecodeError):
        return web.json_response({"error": "json"}, status=400)
    if not webpush.valid_subscription(sub):
        return web.json_response({"error": "subscription"}, status=400)
    await webpush.save_subscription(uid, sub)
    return web.json_response({"ok": True})


def setup(app: web.Application) -> None:
    app.router.add_get("/chat", page_chat)
    app.router.add_get("/chat/login", page_login)
    app.router.add_post("/chat/login", post_login)
    app.router.add_post("/chat/login/phone", post_login_phone)
    app.router.add_post("/chat/login/code", post_login_code)
    app.router.add_get("/chat/logout", logout)
    app.router.add_get("/chat/api/history", api_history)
    app.router.add_get("/chat/api/poll", api_poll)
    app.router.add_post("/chat/api/send", api_send)
    app.router.add_get("/chat/file/{fid}", api_file)
    app.router.add_get("/chat/api/push/key", _need_uid(api_push_key))
    app.router.add_post("/chat/api/push/subscribe", _need_uid(api_push_subscribe))
    app.router.add_get("/portal/bot/api/push/key", _need_uid(api_push_key, admin=True))
    app.router.add_post("/portal/bot/api/push/subscribe", _need_uid(api_push_subscribe, admin=True))
    # чат администратора с ботом — в портале, на админской cookie
    app.router.add_get("/portal/bot", page_admin_chat)
    app.router.add_get("/portal/bot/api/history", _need_uid(api_history.raw, admin=True))
    app.router.add_get("/portal/bot/api/poll", _need_uid(api_poll.raw, admin=True))
    app.router.add_post("/portal/bot/api/send", _need_uid(api_send.raw, admin=True))
    app.router.add_get("/portal/bot/file/{fid}", _need_uid(api_file.raw, admin=True))
