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
import logging
import secrets
import time
from datetime import datetime

from aiogram.client.session.base import BaseSession
from aiogram.methods import (AnswerCallbackQuery, DeleteMessage, EditMessageReplyMarkup, EditMessageText,
                             SendDocument, SendMessage, SendPhoto)
from aiogram.types import (Chat, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiohttp import web

import db
from config import config

log = logging.getLogger(__name__)

COOKIE = "zcw"
ORIGIN: contextvars.ContextVar[int | None] = contextvars.ContextVar("web_origin", default=None)
KEEP_EVENTS = 1500
POLL_SECONDS = 20
SEND_LIMIT = 60          # действий в минуту с одной сессии
_hits: dict[int, list[float]] = {}
_locks: dict[int, asyncio.Lock] = {}
_linked_cache: dict[int, tuple[float, bool]] = {}
_tg_cache: dict[int, tuple[float, int | None]] = {}


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
    return eid


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
                if tg:
                    copy = SendMessage(chat_id=tg, text=getattr(method, "text", None) or getattr(method, "caption", None)
                                       or "Новое сообщение в чате на сайте")
                    asyncio.create_task(self._telegram_quietly(bot, copy, timeout))
            return result
        if sending and await _is_linked_admin(cid):  # уведомление админу: и в Telegram, и в браузер
            result = await self._web(cid, method)
            asyncio.create_task(self._telegram_quietly(bot, method, timeout))
            return result
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

async def new_session(uid: int | None = None) -> str:
    """Выдаёт токен. Без uid — гость: отрицательный id, не пересекается с настоящими Telegram-id."""
    token = secrets.token_urlsafe(32)
    async with db.transaction() as conn:
        cur = await conn.execute("INSERT INTO web_sessions (token_hash, uid) VALUES (?, ?)",
                                 (_hash(token), uid if uid is not None else 0))
        if uid is None:
            await conn.execute("UPDATE web_sessions SET uid=? WHERE id=?", (-cur.lastrowid, cur.lastrowid))
    _linked_cache.clear()
    return token


async def session_uid(request: web.Request) -> int | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (_hash(token),))
    return row["uid"] if row else None


def _set_cookie(request: web.Request, resp: web.StreamResponse, token: str) -> None:
    secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
    resp.set_cookie(COOKIE, token, max_age=60 * 60 * 24 * 90, httponly=True, secure=secure, samesite="Lax", path="/")


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


async def page_chat(request: web.Request) -> web.Response:
    uid = await session_uid(request)
    token = None
    if uid is None:
        token = await new_session()
    html = _ui("chat.html").replace("__TG_URL__", f"https://t.me/{config.bot_username}")
    resp = web.Response(text=html, content_type="text/html",
                        headers={"Cache-Control": "no-cache", "Referrer-Policy": "no-referrer"})
    if token:
        _set_cookie(request, resp, token)
    return resp


async def page_login(request: web.Request) -> web.Response:
    import html as _h
    t = _h.escape(request.query.get("t", ""), quote=True)
    n = "portal" if request.query.get("n") == "portal" else "chat"
    import brand
    title, text = (("Вход администратора", "Нажмите кнопку, чтобы войти под своим аккаунтом администратора.")
                   if n == "portal" else ("Ваш кабинет", "Нажмите кнопку, чтобы открыть кабинет Зооконсьерж на этом телефоне."))
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{brand.NAME} — вход</title>{brand.HEAD}<body style='max-width:420px;margin:10vh auto;padding:16px'>"
            f"<div style='text-align:center;font-size:26px'>{brand.LOGO}</div>"
            f"<h2 style='text-align:center'>{title}</h2><p>{text}</p>"
            f"<form method=post action=/chat/login><input type=hidden name=t value='{t}'><input type=hidden name=n value='{n}'>"
            "<button class='btn' style='width:100%'>Войти</button></form></body>")
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
    token = await new_session(row["tg_id"])
    resp = web.Response(status=302, headers={"Location": "/portal" if form.get("n") == "portal" else "/chat"})
    _set_cookie(request, resp, token)
    return resp


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get(COOKIE)
    if token:
        await db.execute("DELETE FROM web_sessions WHERE token_hash=?", (_hash(token),))
        _linked_cache.clear()
    resp = web.Response(status=302, headers={"Location": "/chat"})
    resp.del_cookie(COOKIE, path="/")
    return resp


# ---------- API страницы ----------

def _need_uid(handler):
    async def wrapper(request: web.Request):
        uid = await session_uid(request)
        if uid is None:
            return web.json_response({"error": "session"}, status=401)
        if request.method == "POST" and request.headers.get("X-Zc") != "1":
            return web.json_response({"error": "csrf"}, status=403)
        return await handler(request, uid)
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


def setup(app: web.Application) -> None:
    app.router.add_get("/chat", page_chat)
    app.router.add_get("/chat/login", page_login)
    app.router.add_post("/chat/login", post_login)
    app.router.add_get("/chat/logout", logout)
    app.router.add_get("/chat/api/history", api_history)
    app.router.add_get("/chat/api/poll", api_poll)
    app.router.add_post("/chat/api/send", api_send)
    app.router.add_get("/chat/file/{fid}", api_file)
