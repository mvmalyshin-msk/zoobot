"""Заявки с сайтов конструктора (сервис workbot) → общий список «Заявки» портала.

Сайт-конструктор после записи заявки у себя отправляет её сюда (POST /api/site-lead, заголовок X-Site-Key).
Ключ задаётся переменной SITE_LEADS_KEY в защищённых переменных хостинга; без неё приём выключен.
Заявка попадает в web_requests (как заявка с /request), админу уходит сообщение в Telegram.
Тексты ответов — это слова незнакомых людей: в базу они идут как данные и экранируются при показе.
"""
import hmac
import logging
import os

from aiohttp import web

import db
import orders
from utils import normalize_phone

log = logging.getLogger(__name__)

MAX_ANSWERS = 30
MAX_VALUE = 500
SITE_PREFIX = "Сайт "


def _clip(x, n: int) -> str:
    return " ".join(str(x).split())[:n]


def parse(body) -> dict | None:
    """Тело запроса → поля заявки или None, если это не заявка."""
    if not isinstance(body, dict) or not isinstance(body.get("answers"), list):
        return None
    site = _clip(body.get("site") or "", 60)
    answers = body["answers"]
    if not site or not answers or len(answers) > MAX_ANSWERS:
        return None
    pairs = []
    for a in answers:
        if not (isinstance(a, (list, tuple)) and len(a) == 2):
            return None
        pairs.append((_clip(a[0], 120), _clip(a[1], MAX_VALUE)))
    if not any(v for _, v in pairs):
        return None

    def by(*words: str) -> str:
        for label, value in pairs:
            if value and any(w in label.lower() for w in words):
                return value
        return ""

    phone_raw = by("телефон", "тел.", "номер")
    name = by("имя") or "Без имени"
    rest = [f"{label}: {value}" for label, value in pairs if value]
    return {
        "site": site, "name": name[:80],
        "phone": normalize_phone(phone_raw) or phone_raw[:30] or "—",
        "pet": by("питомц", "собак", "кошк")[:120],
        "comment": (f"Сайт: {site}\n" + "\n".join(rest))[:1500],
    }


async def post_lead(request: web.Request) -> web.Response:
    key = os.getenv("SITE_LEADS_KEY", "")
    given = request.headers.get("X-Site-Key", "")
    if len(key) < 24 or not hmac.compare_digest(key.encode(), given.encode()):
        return web.Response(status=403, text="forbidden")
    try:
        lead = parse(await request.json())
    except Exception:  # noqa: BLE001 — не JSON, слишком большое тело и т.п.
        lead = None
    if lead is None:
        return web.Response(status=400, text="bad lead")
    async with db.transaction() as conn:
        cur = await conn.execute(
            """INSERT INTO web_requests (service_id, name, phone, contact_pref, address, when_text, pet, comment)
               VALUES (NULL,?,?,?,?,?,?,?)""",
            (lead["name"], lead["phone"], "call", SITE_PREFIX + lead["site"], None, lead["pet"], lead["comment"]))
        rid = cur.lastrowid
    text = f"🌐 Заявка с сайта «{lead['site']}» №{rid}\n{lead['comment'].split(chr(10), 1)[1]}\n" \
           "Свяжитесь с клиентом по телефону."
    try:
        await orders.notify_admins(request.app["bot"], text[:3900], copy_helper=False)  # владельцу заявку сайта конструктора уже прислал сам workbot
    except Exception:  # noqa: BLE001 — заявка уже записана, админ увидит её в портале
        log.exception("Не удалось уведомить админа о заявке с сайта №%s", rid)
    return web.json_response({"id": rid})
