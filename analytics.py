"""Аналитика посещений сайта и бота — своя, без сторонних сервисов и без имён, телефонов и текстов сообщений.

Что копится (таблица analytics_events, хранится 2 года):
  сайт — просмотры страниц, нажатия (текст кнопки/ссылки), глубина прокрутки, какие поля формы начали заполнять,
         отправка форм, секунды на странице, откуда пришли, устройство и браузер (см. webapp/a.js);
  бот  — команды, нажатые кнопки, тип сообщений (текст/фото/…, но не сам текст), шаг диалога, на котором человек был.
Администратор и поисковые роботы не считаются. Сессия — до 30 минут тишины.
Показывается в портале на странице «Аналитика» (/portal/analytics); весь журнал можно скачать в CSV."""
import csv
import io
import json
import logging
import re
import time
import uuid
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from aiohttp import web
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import db
import portal
import webchat
from config import config
from utils import fmt_local

log = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).parent / "webapp"
SESSION_GAP = 1800          # секунд тишины до новой сессии
GAP_CAP = {"web": 60, "bot": 300}   # паузу длиннее считаем уходом: в «время в сервисе» засчитывается не больше
RETENTION_DAYS = 730
MAX_BODY = 16000
WEB_TYPES = {"pv", "click", "scroll", "ping", "leave", "focus", "submit"}
_ID = re.compile(r"^[a-z0-9]{8,40}$")
_ROBOT = re.compile(r"bot|crawl|spider|slurp|headless|lighthouse|preview|facebookexternalhit|curl|wget|python|httpclient|monitor|uptime", re.I)

PERIODS = {"today": ("Сегодня", 0), "7": ("7 дней", 6), "30": ("30 дней", 29), "all": ("Всё время", None)}
BTN_NAMES = {
    "menu:order": "Заказать услугу", "menu:orders": "Мои заказы", "menu:profile": "Профиль", "menu:home": "В меню",
    "menu:repeat": "Повторить заказ", "svc": "Выбор услуги", "cat": "Вид услуги", "cal": "Календарь", "day": "Выбор дня",
    "ord:ok": "Подтвердить заказ", "ord:edit": "Внести правки", "ord:cancel": "Отменить оформление", "ord:back": "Назад",
    "ord:addr": "Изменить адрес", "ord:when": "Изменить время", "ord:pet": "Питомец", "ord:mkad": "За МКАД",
    "pdn:agree": "Согласие на данные", "pol:show": "Политика", "pay": "Оплата", "cancel": "Отмена заказа",
    "cancel_yes": "Подтвердил отмену", "pick": "Выбор питомца", "sz": "Размер собаки", "qty": "Длительность", "rev": "Оценка",
}
STATE_NAMES = {None: "—", "": "—"}
WEEK = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


# ---------- Разбор браузера ----------

def is_robot(ua: str) -> bool:
    return not ua or bool(_ROBOT.search(ua))


def classify_ua(ua: str) -> tuple[str, str, str]:
    """(устройство, браузер, система) по строке User-Agent. Грубо, но достаточно для картины «кто к нам заходит»."""
    ua = ua or ""
    dev = "tablet" if re.search(r"iPad|Tablet", ua) else "mobile" if re.search(r"Mobi|iPhone|Android", ua) else "desktop"
    if re.search(r"iPhone|iPad|iPod", ua):
        os_ = "iOS"
    elif "Android" in ua:
        os_ = "Android"
    elif "Windows" in ua:
        os_ = "Windows"
    elif "Mac OS X" in ua or "Macintosh" in ua:
        os_ = "macOS"
    elif "Linux" in ua:
        os_ = "Linux"
    else:
        os_ = "другая"
    if re.search(r"YaBrowser", ua):
        br = "Яндекс Браузер"
    elif re.search(r"Edg/", ua):
        br = "Edge"
    elif re.search(r"OPR/|Opera", ua):
        br = "Opera"
    elif re.search(r"CriOS|Chrome/", ua):
        br = "Chrome"
    elif re.search(r"FxiOS|Firefox/", ua):
        br = "Firefox"
    elif "Safari/" in ua:
        br = "Safari"
    else:
        br = "другой"
    return dev, br, os_


DIRECT = "Напрямую (адрес или закладка)"
_SOURCES = (("yandex", "Яндекс (поиск)"), ("ya.ru", "Яндекс (поиск)"), ("google", "Google (поиск)"),
            ("t.me", "Telegram"), ("telegram", "Telegram"), ("vk.com", "ВКонтакте"), ("vk", "ВКонтакте"),
            ("avito", "Авито"), ("profi", "Профи"), ("2gis", "2ГИС"))
DEVICE_NAMES = {"mobile": "Телефон", "tablet": "Планшет", "desktop": "Компьютер"}


def source_label(ref: str | None) -> str:
    """Понятное имя источника: адрес сайта или метка utm_source → «Яндекс (поиск)», «Telegram», «Напрямую»."""
    ref = (ref or "").strip().lower()
    if not ref:
        return DIRECT
    for key, name in _SOURCES:
        if key in ref:
            return name
    return ref


# ---------- Приём событий сайта ----------

_hits: dict[str, list[float]] = {}


def _limited(ip: str) -> bool:
    """Не больше 240 событий в минуту с одного адреса: защита от накрутки и мусора. Адрес не сохраняется."""
    now = time.monotonic()
    q = [t for t in _hits.get(ip, []) if now - t < 60]
    q.append(now)
    _hits[ip] = q
    if len(_hits) > 5000:
        _hits.clear()
    return len(q) > 240


def _clip(value, n: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())[:n]
    return text or None


def _int(value, hi: int = 86400) -> int | None:
    try:
        return max(0, min(hi, int(value)))
    except (TypeError, ValueError):
        return None


OWNER_COOKIE = "zno"   # метка «это устройство владельца»: ставится при заходе в портал, живёт год, заходы с неё не считаются
_ignore_cache: dict[int, tuple[float, bool]] = {}


async def is_ignored(tg_id: int) -> bool:
    """Администратор и учётки, помеченные владельцем (его клиентский профиль), в аналитику не попадают."""
    if tg_id in config.admin_ids:
        return True
    now = time.time()
    hit = _ignore_cache.get(tg_id)
    if hit and now - hit[0] < 60:
        return hit[1]
    row = await db.fetch_one("SELECT 1 AS x FROM analytics_ignore WHERE tg_id=?", (tg_id,))
    _ignore_cache[tg_id] = (now, bool(row))
    return bool(row)


async def ignore_user(tg_id: int) -> None:
    """Пометить учётку как свою: дальше её не считаем, а уже записанные действия в боте стираем."""
    await db.execute("INSERT OR IGNORE INTO analytics_ignore (tg_id) VALUES (?)", (tg_id,))
    await db.execute("DELETE FROM analytics_events WHERE source='bot' AND vid=?", (f"tg:{tg_id}",))
    _ignore_cache[tg_id] = (time.time(), True)


_ignored_vids: set[str] = set()


async def is_ignored_vid(vid: str) -> bool:
    if vid in _ignored_vids:
        return True
    if await db.fetch_one("SELECT 1 AS x FROM analytics_ignore_vid WHERE vid=?", (vid,)):
        _ignored_vids.add(vid)
        return True
    return False


@portal.admin_page
async def api_own_vid(request: web.Request, uid: int) -> web.Response:
    """Браузер владельца присылает свой код посетителя: прошлые заходы с него стираются, новые не считаются."""
    try:
        vid = str((await request.json())["v"])
    except (ValueError, KeyError, TypeError):
        return web.json_response({"error": "bad"}, status=400)
    if not _ID.match(vid):
        return web.json_response({"error": "bad"}, status=400)
    await db.execute("INSERT OR IGNORE INTO analytics_ignore_vid (vid) VALUES (?)", (vid,))
    await db.execute("DELETE FROM analytics_events WHERE source='web' AND vid=?", (vid,))
    _ignored_vids.add(vid)
    return web.json_response({"ok": True})


@portal.admin_page
async def api_reset(request: web.Request, uid: int) -> web.Response:
    """Начать с чистого листа: стирает журнал посещений сайта (бота не трогает); свои устройства остаются помеченными."""
    await db.execute("DELETE FROM analytics_events WHERE source='web'")
    return web.json_response({"ok": True})


async def mark_owner(request: web.Request, resp: web.StreamResponse) -> None:
    """Вызывается на страницах портала: помечает устройство владельца и его клиентскую учётку в этом же браузере."""
    try:
        secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
        if not request.cookies.get(OWNER_COOKIE):
            resp.set_cookie(OWNER_COOKIE, "1", max_age=60 * 60 * 24 * 365, httponly=True, secure=secure,
                            samesite="Lax", path="/")
        uid = await webchat.session_uid(request)             # клиентская учётка в том же браузере
        if uid and not await is_ignored(uid):
            await ignore_user(uid)
    except Exception:
        log.exception("Не удалось пометить устройство владельца")


async def post_event(request: web.Request) -> web.Response:
    if request.content_length and request.content_length > MAX_BODY:
        return web.Response(status=413)
    raw = await request.read()
    if len(raw) > MAX_BODY:
        return web.Response(status=413)
    try:
        data = json.loads(raw.decode("utf-8"))
        vid, sid, items = str(data["v"]), str(data["s"]), data["d"]
        if not (_ID.match(vid) and _ID.match(sid) and isinstance(items, list)):
            raise ValueError
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return web.Response(status=400)
    ua = request.headers.get("User-Agent", "")
    if is_robot(ua) or request.cookies.get(webchat.ADMIN_COOKIE) or request.cookies.get(OWNER_COOKIE):
        return web.Response(status=204)                  # роботов, администратора и устройства владельца не считаем
    if await is_ignored_vid(vid):
        return web.Response(status=204)                  # браузер владельца по коду посетителя
    if request.cookies.get(webchat.COOKIE):
        uid = await webchat.session_uid(request)
        if uid and await is_ignored(uid):                # клиентская учётка владельца
            return web.Response(status=204)
    fwd = request.headers.get("X-Forwarded-For", "")
    if _limited((fwd.split(",")[0].strip() or request.remote or "?")[:64]):
        return web.Response(status=429)
    dev, br, os_ = classify_ua(ua)
    for it in items[:50]:
        if not isinstance(it, dict) or it.get("t") not in WEB_TYPES:
            continue
        path = _clip(it.get("p"), 120)
        if not path or not path.startswith("/") or path.startswith(("/portal", "/api")):
            continue
        t = it["t"]
        value = _int(it.get("n")) if t in ("scroll", "ping", "leave") else None
        target = _clip(it.get("g"), 120) if t in ("click", "focus", "submit") else None
        ref = _clip(it.get("r"), 80) if t == "pv" else None
        await db.execute(
            "INSERT INTO analytics_events (source, vid, sid, type, path, target, value, device, browser, os, ref) "
            "VALUES ('web',?,?,?,?,?,?,?,?,?,?)", (vid, sid, t, path, target, value, dev, br, os_, ref))
    return web.Response(status=204)


async def tracker_js(request: web.Request) -> web.Response:
    return web.FileResponse(WEBAPP_DIR / "a.js", headers={"Cache-Control": "public, max-age=3600",
                                                         "Content-Type": "application/javascript; charset=utf-8"})


# ---------- События бота ----------

_bot_sessions: dict[int, tuple[str, float]] = {}


def norm_callback(data: str | None) -> str:
    """Кнопка без «хвоста» (номер услуги, дата): 'svc:12' → 'svc'; известные пары вроде 'ord:ok' остаются как есть."""
    if not data:
        return "—"
    if data in BTN_NAMES:
        return data
    return data.split(":")[0][:40] or "—"


async def _bot_session(tg_id: int) -> str:
    now = time.time()
    cached = _bot_sessions.get(tg_id)
    if cached is None:
        row = await db.fetch_one("SELECT sid, CAST(strftime('%s', ts) AS INTEGER) AS t FROM analytics_events "
                                 "WHERE source='bot' AND vid=? ORDER BY id DESC LIMIT 1", (f"tg:{tg_id}",))
        cached = (row["sid"], float(row["t"])) if row else None
    sid = cached[0] if cached and now - cached[1] <= SESSION_GAP else "b" + uuid.uuid4().hex[:15]
    _bot_sessions[tg_id] = (sid, now)
    return sid


async def bot_event(tg_id: int, kind: str, target: str | None, state: str | None = None) -> None:
    """Одно действие человека в боте. Никогда не ломает бота: любая ошибка только в журнал."""
    try:
        if await is_ignored(tg_id):
            return
        sid = await _bot_session(tg_id)
        await db.execute("INSERT INTO analytics_events (source, vid, sid, type, target, state) VALUES ('bot',?,?,?,?,?)",
                         (f"tg:{tg_id}", sid, kind, _clip(target, 60), _clip(state, 60)))
    except Exception:
        log.exception("Не удалось записать событие аналитики")


class BotStatsMiddleware(BaseMiddleware):
    """Считает действия в боте: команды, нажатия кнопок, тип сообщений. Текст сообщений не сохраняется."""

    async def __call__(self, handler, event, data):
        try:
            user = event.from_user
            state = data.get("raw_state")
            if isinstance(event, CallbackQuery):
                await bot_event(user.id, "btn", norm_callback(event.data), state)
            elif isinstance(event, Message):
                text = event.text or ""
                if text.startswith("/"):
                    await bot_event(user.id, "cmd", text.split()[0].split("@")[0], state)
                else:
                    kind = ("text" if text else "photo" if event.photo else "location" if event.location else
                            "contact" if event.contact else "voice" if event.voice else "other")
                    await bot_event(user.id, "msg", kind, state)
        except Exception:
            log.exception("Аналитика бота")
        return await handler(event, data)


# ---------- Подсчёт ----------

def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def since_for(period: str) -> str:
    """Начало периода в UTC (как хранится в базе): с местной полуночи."""
    days = PERIODS.get(period, PERIODS["7"])[1]
    if days is None:
        return "2000-01-01 00:00:00"
    today = datetime.now(config.tz).date()
    return _utc(datetime.combine(today - timedelta(days=days), dtime.min, tzinfo=config.tz))


def _offset() -> int:
    return int(datetime.now(config.tz).utcoffset().total_seconds())


async def _all(sql, params=()):
    return await db.fetch_all(sql, tuple(params))


FORM_FIELDS = (("service_id", "Услуга"), ("when", "Когда нужно"), ("address", "Адрес"), ("pet", "Питомец"),
               ("name", "Имя"), ("phone", "Телефон"), ("comment", "Комментарий"))


def _summary_web(m: dict) -> list[str]:
    if not m["visitors"]:
        return ["За этот период на сайт пока никто не заходил."]
    out = [f"Сайт посмотрели {m['visitors']} уникальных посетителей, всего визитов: {m['sessions']}."]
    if m["sources"]:
        out.append("Откуда пришли: " + ", ".join(f"{r['name']} — {r['n']}" for r in m["sources"][:3]) + ".")
    if m["devices"]:
        out.append("С каких устройств: " + ", ".join(f"{d['name']} {d['pct']}%" for d in m["devices"]) + ".")
    steps = dict(m["form_steps"])
    out.append(f"Форму заявки открыли {steps['Открыли форму заявки']}, заявку отправили {steps['Отправили заявку']}.")
    return out


def _summary_bot(m: dict) -> list[str]:
    if not m["visitors"]:
        return ["За этот период в бота пока никто не писал."]
    f = dict(m["funnel"])
    return [f"Бота открыли {m['visitors']} человек.",
            f"Нажали «Заказать услугу»: {f['Нажали «Заказать услугу»']}, оформили заказ: {f['Оформили заказ']}."]


async def collect(source: str, period: str = "7") -> dict:
    since = since_for(period)
    src = (source, since)
    off = _offset()
    one = lambda sql, p=src: db.fetch_one(sql, tuple(p))  # noqa: E731
    m: dict = {"source": source, "period": period}
    m["visitors"] = (await one("SELECT COUNT(DISTINCT vid) AS n FROM analytics_events WHERE source=? AND ts>=?"))["n"]
    m["sessions"] = (await one("SELECT COUNT(DISTINCT sid) AS n FROM analytics_events WHERE source=? AND ts>=?"))["n"]
    m["events"] = (await one("SELECT COUNT(*) AS n FROM analytics_events WHERE source=? AND ts>=?"))["n"]
    m["pageviews"] = (await one("SELECT COUNT(*) AS n FROM analytics_events WHERE source=? AND ts>=? AND type='pv'"))["n"]
    m["new_visitors"] = (await one(
        "SELECT COUNT(*) AS n FROM (SELECT vid, MIN(ts) AS f FROM analytics_events WHERE source=? GROUP BY vid HAVING f>=?)"))["n"]

    durs = await _all(
        """WITH e AS (SELECT sid, CAST(strftime('%s', ts) AS INTEGER) AS t FROM analytics_events WHERE source=? AND ts>=?),
                g AS (SELECT sid, t - LAG(t) OVER (PARTITION BY sid ORDER BY t) AS gap FROM e)
           SELECT sid, SUM(MIN(COALESCE(gap, 0), ?)) AS dur FROM g GROUP BY sid""", (*src, GAP_CAP[source]))
    vals = [d["dur"] for d in durs]
    m["avg_session"] = round(sum(vals) / len(vals)) if vals else 0
    m["quick"] = 0
    if source == "web":
        pv = {r["sid"]: r["n"] for r in await _all(
            "SELECT sid, COUNT(*) AS n FROM analytics_events WHERE source='web' AND ts>=? AND type='pv' GROUP BY sid", (since,))}
        m["quick"] = sum(1 for d in durs if pv.get(d["sid"], 0) <= 1 and d["dur"] < 10)

    m["days"] = await _all(
        f"SELECT date(ts, '{off:+d} seconds') AS day, COUNT(DISTINCT vid) AS visitors, COUNT(DISTINCT sid) AS sessions "
        "FROM analytics_events WHERE source=? AND ts>=? GROUP BY day ORDER BY day", src)
    m["hours"] = await _all(
        f"SELECT CAST(strftime('%H', ts, '{off:+d} seconds') AS INTEGER) AS h, COUNT(DISTINCT sid) AS n "
        "FROM analytics_events WHERE source=? AND ts>=? GROUP BY h ORDER BY h", src)
    devs = await _all(
        "SELECT device AS name, COUNT(DISTINCT sid) AS n FROM analytics_events WHERE source='web' AND ts>=? AND device IS NOT NULL "
        "GROUP BY device ORDER BY n DESC", (since,)) if source == "web" else []
    dev_total = sum(d["n"] for d in devs) or 1
    m["devices"] = [{"name": DEVICE_NAMES.get(d["name"], d["name"]), "n": d["n"], "pct": round(d["n"] / dev_total * 100)} for d in devs]
    m["browsers"] = await _all(
        "SELECT browser || ' · ' || os AS name, COUNT(DISTINCT sid) AS n FROM analytics_events WHERE source='web' AND ts>=? "
        "AND browser IS NOT NULL GROUP BY name ORDER BY n DESC LIMIT 8", (since,)) if source == "web" else []

    if source == "web":
        m["pages"] = await _all(
            """SELECT p.path, p.views, p.visitors, COALESCE(t.avg_s, 0) AS avg_s FROM
                 (SELECT path, COUNT(*) AS views, COUNT(DISTINCT vid) AS visitors FROM analytics_events
                  WHERE source='web' AND ts>=? AND type='pv' GROUP BY path) p
               LEFT JOIN (SELECT path, CAST(ROUND(AVG(m)) AS INTEGER) AS avg_s FROM
                  (SELECT sid, path, MAX(value) AS m FROM analytics_events WHERE source='web' AND ts>=? AND type='leave' GROUP BY sid, path)
                  GROUP BY path) t ON t.path=p.path ORDER BY p.views DESC, p.path LIMIT 15""", (since, since))
        m["exits"] = await _all(
            """SELECT path, COUNT(*) AS n FROM analytics_events e WHERE source='web' AND ts>=? AND type='pv'
               AND id=(SELECT MAX(id) FROM analytics_events x WHERE x.sid=e.sid AND x.type='pv') GROUP BY path ORDER BY n DESC LIMIT 10""", (since,))
        m["clicks"] = await _all(
            "SELECT target, COUNT(*) AS n, COUNT(DISTINCT vid) AS people FROM analytics_events WHERE source='web' AND ts>=? "
            "AND type='click' AND target IS NOT NULL GROUP BY target ORDER BY n DESC LIMIT 15", (since,))
        m["scroll"] = {r["value"]: r["n"] for r in await _all(
            "SELECT value, COUNT(DISTINCT sid || path) AS n FROM analytics_events WHERE source='web' AND ts>=? AND type='scroll' "
            "GROUP BY value", (since,))}
        first_pv = await _all(
            """SELECT COALESCE(ref, '') AS ref, COUNT(*) AS n FROM analytics_events e WHERE source='web' AND ts>=?
               AND type='pv' AND id=(SELECT MIN(id) FROM analytics_events x WHERE x.sid=e.sid AND x.type='pv')
               GROUP BY ref""", (since,))
        by_src: dict[str, int] = {}
        for r in first_pv:
            by_src[source_label(r["ref"])] = by_src.get(source_label(r["ref"]), 0) + r["n"]
        m["sources"] = [{"name": k, "n": v} for k, v in sorted(by_src.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
        m["fields"] = await _all(
            "SELECT target, COUNT(DISTINCT sid) AS n FROM analytics_events WHERE source='web' AND ts>=? AND type='focus' AND target IS NOT NULL "
            "GROUP BY target ORDER BY n DESC LIMIT 12", (since,))

        def sess(where, extra=()):
            return db.fetch_one(f"SELECT COUNT(DISTINCT sid) AS n FROM analytics_events WHERE source='web' AND ts>=? AND {where}", (since, *extra))
        m["funnel"] = [
            ("Зашли на главную", (await sess("type='pv' AND path='/'"))["n"]),
            ("Открыли форму заявки", (await sess("type='pv' AND path='/request'"))["n"]),
            ("Начали заполнять", (await sess("type='focus' AND path='/request'"))["n"]),
            ("Отправили заявку", (await sess("type='submit' AND path='/request'"))["n"]),
        ]
        m["stops"] = []
        form = (await sess("type='pv' AND path='/request'"))["n"]
        steps = [("Открыли форму заявки", form)]
        for field, label in FORM_FIELDS:
            steps.append((label, (await sess("type='focus' AND path='/request' AND target=?", (field,)))["n"]))
        steps.append(("Отправили заявку", (await sess("type='submit' AND path='/request'"))["n"]))
        m["form_steps"] = steps
        m["summary"] = _summary_web(m)
    else:
        m["buttons"] = await _all(
            "SELECT target, COUNT(*) AS n FROM analytics_events WHERE source='bot' AND ts>=? AND type='btn' GROUP BY target ORDER BY n DESC LIMIT 15", (since,))
        m["commands"] = await _all(
            "SELECT target, COUNT(*) AS n FROM analytics_events WHERE source='bot' AND ts>=? AND type='cmd' GROUP BY target ORDER BY n DESC LIMIT 10", (since,))
        m["messages"] = await _all(
            "SELECT target, COUNT(*) AS n FROM analytics_events WHERE source='bot' AND ts>=? AND type='msg' GROUP BY target ORDER BY n DESC", (since,))
        m["stops"] = await _all(
            """SELECT target AS what, state, COUNT(*) AS n FROM analytics_events e WHERE source='bot' AND ts>=?
               AND id=(SELECT MAX(id) FROM analytics_events x WHERE x.sid=e.sid) GROUP BY target, state ORDER BY n DESC LIMIT 12""", (since,))

        def users(where):
            return db.fetch_one(f"SELECT COUNT(DISTINCT vid) AS n FROM analytics_events WHERE source='bot' AND ts>=? AND {where}", (since,))
        orders_n = (await db.fetch_one(
            "SELECT COUNT(DISTINCT user_id) AS n FROM orders WHERE created_at>=?", (since,)))["n"]
        m["funnel"] = [
            ("Открыли бота", m["visitors"]),
            ("Нажали «Заказать услугу»", (await users("target='menu:order'"))["n"]),
            ("Выбрали услугу", (await users("target='svc'"))["n"]),
            ("Дошли до подтверждения", (await users("target='ord:ok'"))["n"]),
            ("Оформили заказ", orders_n),
        ]
        m["summary"] = _summary_bot(m)
    return m


async def cleanup() -> int:
    """Журнал старше двух лет удаляется (запускается раз в сутки). Возвращает, сколько записей убрано."""
    cut = f"-{RETENTION_DAYS} days"
    n = (await db.fetch_one("SELECT COUNT(*) AS n FROM analytics_events WHERE ts < datetime('now', ?)", (cut,)))["n"]
    if n:
        await db.execute("DELETE FROM analytics_events WHERE ts < datetime('now', ?)", (cut,))
    return n


# ---------- Страница в портале ----------

CSS = """<style>
.an-tabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px}.an-tabs a{padding:7px 14px;border-radius:999px;background:var(--peach);color:var(--ink);text-decoration:none;font-weight:700;font-size:14px}
.an-tabs a.on{background:var(--ink);color:#fff}
.an-cards{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin:0 0 14px}
.an-card{background:var(--card);border:1.5px solid var(--line);border-radius:16px;padding:12px 14px}
.an-card b{display:block;font-size:1.55rem;line-height:1.1}.an-card span{color:var(--mute);font-size:13px}
.an-block{margin:0 0 16px}.an-block h2{margin:0 0 6px}
.an-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;padding:7px 0;border-bottom:1px solid var(--line);font-size:15px}
.an-row .nm{overflow-wrap:anywhere}.an-row .v{font-weight:800;text-align:right}
.an-row .bar{grid-column:1/3;height:6px;border-radius:3px;background:var(--peach);overflow:hidden}.an-row .bar i{display:block;height:100%;background:var(--coral)}
.an-row small{color:var(--mute)}
.an-days{display:flex;align-items:flex-end;gap:3px;height:110px;padding:6px 4px 0}.an-days div{flex:1;min-width:4px;background:var(--coral);border-radius:3px 3px 0 0;position:relative}
.an-days div:hover::after{content:attr(title);position:absolute;bottom:100%;left:50%;transform:translateX(-50%);background:var(--ink);color:#fff;padding:2px 6px;border-radius:6px;font-size:12px;white-space:nowrap}
.an-ax{display:flex;justify-content:space-between;color:var(--mute);font-size:12px;padding:2px 4px}
.an-fun .an-row .bar i{background:var(--nanny)}
@media(min-width:700px){.an-cards{grid-template-columns:repeat(4,1fr)}}
</style>"""


def dur_text(sec: int) -> str:
    sec = int(sec or 0)
    return f"{sec // 60} мин {sec % 60:02d} с" if sec >= 60 else f"{sec} с"


def _rows(items, name_key, value_key="n", *, total=None, fmt=None, extra=None) -> str:
    if not items:
        return '<p class="muted">Пока нет данных.</p>'
    top = max((i[value_key] for i in items), default=0) or 1
    out = []
    for i in items:
        name = fmt(i) if fmt else i[name_key]
        sub = f" <small>{portal._e(extra(i))}</small>" if extra else ""
        pct = round(i[value_key] / (total or top) * 100) if (total or top) else 0
        out.append(f'<div class="an-row"><span class="nm">{portal._e(name)}{sub}</span><span class="v">{i[value_key]}</span>'
                   f'<span class="bar"><i style="width:{min(pct, 100)}%"></i></span></div>')
    return "".join(out)


def _block(title: str, inner: str, hint: str = "") -> str:
    h = f'<p class="muted" style="margin:0 0 6px;font-size:14px">{portal._e(hint)}</p>' if hint else ""
    return f'<div class="an-block"><h2>{portal._e(title)}</h2>{h}<div class="card" style="padding:4px 14px 8px">{inner}</div></div>'


def _funnel(steps) -> str:
    first = steps[0][1] or 1
    rows = "".join(
        f'<div class="an-row"><span class="nm">{portal._e(n)}</span><span class="v">{c}'
        f'{f" · {round(c / first * 100)}%" if first and c else ""}</span><span class="bar"><i style="width:{round(c / first * 100) if first else 0}%"></i></span></div>'
        for n, c in steps)
    return f'<div class="an-fun">{rows}</div>'


def _days_chart(days) -> str:
    if not days:
        return '<p class="muted">Пока нет данных.</p>'
    top = max(d["visitors"] for d in days) or 1
    bars = "".join(f'<div style="height:{max(4, round(d["visitors"] / top * 100))}%" title="{d["day"][8:]}.{d["day"][5:7]}: {d["visitors"]} чел., {d["sessions"]} визитов"></div>'
                   for d in days)
    a, b = days[0]["day"], days[-1]["day"]
    return f'<div class="an-days">{bars}</div><div class="an-ax"><span>{a[8:]}.{a[5:7]}</span><span>{b[8:]}.{b[5:7]}</span></div>'


def _hours_chart(hours) -> str:
    by = {h["h"]: h["n"] for h in hours}
    top = max(by.values(), default=0) or 1
    bars = "".join(f'<div style="height:{max(3, round(by.get(h, 0) / top * 100))}%;opacity:{1 if by.get(h) else .25}" title="{h}:00 — {by.get(h, 0)}"></div>' for h in range(24))
    return f'<div class="an-days">{bars}</div><div class="an-ax"><span>0:00</span><span>12:00</span><span>23:00</span></div>'


def render(m: dict) -> str:
    src, per = m["source"], m["period"]
    tabs = "".join(f'<a class="{"on" if k == per else ""}" href="/portal/analytics?src={src}&p={k}">{v[0]}</a>' for k, v in PERIODS.items())
    srcs = "".join(f'<a class="{"on" if k == src else ""}" href="/portal/analytics?src={k}&p={per}">{v}</a>'
                   for k, v in (("web", "🌐 Сайт"), ("bot", "🤖 Бот")))
    cards = [("Посетители", m["visitors"], "уникальных людей"), ("Сессии", m["sessions"], "визитов (до 30 мин тишины)")]
    cards.append(("Просмотры страниц" if src == "web" else "Действия", m["pageviews"] if src == "web" else m["events"], ""))
    cards.append(("Средняя длительность", dur_text(m["avg_session"]), "время в сервисе за визит"))
    cards.append(("Новые", m["new_visitors"], "впервые за всё время"))
    if src == "web":
        cards.append(("Быстро ушли", m["quick"], "1 страница и меньше 10 секунд"))
    short = '<div class="an-block"><h2>Коротко</h2><div class="card" style="padding:10px 14px">' + "".join(
        f"<p style=\"margin:4px 0\">{portal._e(x)}</p>" for x in m["summary"]) + "</div></div>"
    html = [CSS, f'<div class="an-tabs">{srcs}</div><div class="an-tabs">{tabs}</div>', short,
            '<div class="an-cards">' + "".join(f'<div class="an-card"><b>{v}</b>{portal._e(k)}<br><span>{portal._e(h)}</span></div>' for k, v, h in cards) + "</div>",
            _block("Посетители по дням", _days_chart(m["days"])), _block("В какое время заходят", _hours_chart(m["hours"]), "число визитов по часам (местное время)")]
    if src == "web":
        html += [
            _block("Популярные страницы", _rows(m["pages"], "path", "views", extra=lambda i: f"{i['visitors']} чел. · на странице ≈ {dur_text(i['avg_s'])}"),
                   "просмотры; рядом — сколько людей и сколько секунд в среднем провели"),
            _block("Куда нажимали", _rows(m["clicks"], "target", "n", extra=lambda i: f"{i['people']} чел."), "кнопки и ссылки: текст → куда ведёт"),
            _block("Где уходят", _rows(m["exits"], "path"), "последняя страница визита: на ней человек остановился"),
            _block("До какого вопроса дошли", _funnel(m["form_steps"]),
                   "сколько человек открыли форму, дошли до каждого вопроса и отправили заявку (что вписано в поля — не сохраняется)"),
            _block("Прокрутка", _rows([{"name": f"дочитали до {k}%", "n": m["scroll"].get(k, 0)} for k in (25, 50, 75, 100)], "name"),
                   "сколько страниц были прокручены до этой глубины"),
            _block("Откуда пришли", _rows(m["sources"], "name"), "первая страница визита: из поиска, Telegram, Авито или напрямую"),
            _block("С каких устройств", _rows(m["devices"], "name", extra=lambda i: f"{i['pct']}%") + _rows(m["browsers"], "name"),
                   "визиты: телефон, компьютер, планшет; ниже — браузеры")]
    else:
        html += [
            _block("Путь клиента", _funnel(m["funnel"]), "сколько людей дошли до каждого шага заказа"),
            _block("Кнопки", _rows(m["buttons"], "target", fmt=lambda i: BTN_NAMES.get(i["target"], i["target"])), "что нажимали в боте"),
            _block("Команды", _rows(m["commands"], "target")),
            _block("На чём остановились", _rows(m["stops"], "what", fmt=lambda i: BTN_NAMES.get(i["what"], i["what"] or "—"),
                                                extra=lambda i: f"шаг: {i['state'] or '—'}"),
                   "последнее действие визита: где человек перестал отвечать"),
            _block("Сообщения", _rows(m["messages"], "target", fmt=lambda i: {"text": "текст", "photo": "фото", "location": "геопозиция", "contact": "контакт", "voice": "голосовое"}.get(i["target"], i["target"])),
                   "тип сообщений; сам текст не сохраняется")]
    html.append('<p><button class="btn small ghost" type="button" id="anreset">🧹 Начать с чистого листа</button></p>'
                '<script>document.getElementById("anreset").onclick=function(){if(!confirm("Стереть всю статистику сайта и начать заново? Это нельзя отменить."))return;'
                'fetch("/portal/api/analytics/reset",{method:"POST",credentials:"same-origin"}).then(function(){location.reload()})};</script>')
    html.append(f'<p><a class="btn small ghost" href="/portal/analytics.csv?p={per}">⬇️ Скачать журнал (CSV)</a></p>'
                '<p class="muted" style="font-size:13px">Собственный счётчик: без сторонних сервисов, без имён, телефонов и текстов сообщений; '
                'администратор и роботы не считаются; данные хранятся 2 года.</p>')
    return "".join(html)


@portal.admin_page
async def analytics_page(request: web.Request, uid: int) -> web.Response:
    src = request.query.get("src", "web")
    src = src if src in ("web", "bot") else "web"
    per = request.query.get("p", "7")
    per = per if per in PERIODS else "7"
    body = f"<h1>Аналитика</h1>{render(await collect(src, per))}"
    return portal._html_resp(portal.page("Аналитика", body, "stats"))


@portal.admin_page
async def analytics_csv(request: web.Request, uid: int) -> web.Response:
    per = request.query.get("p", "all")
    since = since_for(per if per in PERIODS else "all")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["время", "источник", "посетитель", "сессия", "событие", "страница", "что нажали / поле", "значение", "шаг бота", "устройство", "браузер", "система", "откуда"])
    for r in await db.fetch_all("SELECT * FROM analytics_events WHERE ts>=? ORDER BY id LIMIT 200000", (since,)):
        cells = [fmt_local(r["ts"]), r["source"], r["vid"], r["sid"], r["type"], r["path"], r["target"], r["value"], r["state"],
                 r["device"], r["browser"], r["os"], r["ref"]]
        w.writerow([("'" + c if isinstance(c, str) and c[:1] in "=+-@" else c) for c in cells])
    return web.Response(body=("﻿" + out.getvalue()).encode("utf-8"), headers={
        "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": 'attachment; filename="analytics.csv"', "Cache-Control": "no-store"})


def setup(app: web.Application) -> None:
    app.router.add_post("/api/a", post_event)
    app.router.add_get("/a.js", tracker_js)


def setup_portal(app: web.Application) -> None:
    app.router.add_get("/portal/analytics", analytics_page)
    app.router.add_get("/portal/analytics.csv", analytics_csv)
    app.router.add_post("/portal/api/analytics/own", api_own_vid)
    app.router.add_post("/portal/api/analytics/reset", api_reset)
