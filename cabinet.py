"""Личный кабинет клиента на сайте: питомцы и история заказов.

Один клиент — один кабинет: если веб-гость привязан к Telegram (users.verified_tg_id),
кабинет тот же, что у клиента в Telegram. Иначе — кабинет самой сессии (гость видит пустой)."""
import html as _h
from urllib.parse import quote, urlparse

from aiohttp import web

import brand
import ribbons
import orders
import pricing
import repo
import texts
import webchat
from utils import fmt_local


async def canonical_tg(uid: int) -> int:
    """Чей это кабинет: Telegram-клиента, к которому привязана сессия, или сам uid."""
    return await webchat._verified_tg(uid) or uid


def _e(v) -> str:
    return _h.escape("" if v is None else str(v), quote=True)


def _pets_html(pets: list[dict]) -> str:
    if not pets:
        return "<p class=muted>Питомцев пока нет. Добавить их можно в боте.</p>"
    items = []
    for p in pets:
        size = f", {p['size']}" if p.get("size") else ""
        breed = f", {p['breed']}" if p.get("breed") else ""
        items.append(f"<li><b>{_e(p['name'])}</b> — {_e(p['species'])}{_e(size)}{_e(breed)}</li>")
    return "<ul>" + "".join(items) + "</ul>"


def _history_html(events: list[dict]) -> str:
    lines = []
    for h in events:
        what = texts.STATUS_LABELS.get(h["to_status"], h["to_status"])
        note = f"<br>{_e(h['note'])}" if h.get("note") else ""
        lines.append(f"<div class=muted>{_e(fmt_local(h['created_at']))} · {_e(what)}{note}</div>")
    return "".join(lines)


def _actions_html(oid: int, status: str) -> str:
    out = []
    if status in ("created", "confirmed"):
        out.append(f"<form method=post action='/cabinet/order/{oid}'>"
                   "<button name=action value=cancel class='btn small ghost'>Отменить заказ</button></form>")
    if status in orders.ACTIVE_STATUSES:
        out.append(f"<form method=post action='/cabinet/order/{oid}'>"
                   "<textarea name=text maxlength='500' placeholder='Что случилось или на какое время перенести'"
                   " style='width:100%;min-height:60px'></textarea>"
                   "<button name=action value=problem class='btn small ghost'>⚠️ Сообщить о проблеме</button> "
                   "<button name=action value=move class='btn small ghost'>🔁 Попросить перенос</button></form>")
    return "".join(out)


async def _orders_html(rows: list[dict]) -> str:
    if not rows:
        return "<p class=muted>Заказов пока нет.</p>"
    items = []
    for o in rows:
        pet = f" · {o['pet_name']}" if o.get("pet_name") else ""
        status = texts.STATUS_LABELS.get(o["status"], o["status"])
        hist = _history_html(await orders.order_history(o["id"]))
        items.append(f"<li>№{_e(o['id'])} · {_e(o['name'])}{_e(pet)} · {_e(fmt_local(o['start_at']))}"
                     f" · {_e(pricing.format_range_short(o['price_total']))}<br><b>{_e(status)}</b>"
                     f"<details><summary>История</summary>{hist}</details>"
                     f"{_actions_html(o['id'], o['status'])}</li>")
    return "<ul>" + "".join(items) + "</ul>"


async def order_action(request: web.Request) -> web.Response:
    """Отмена и заявки по заказу с сайта. Защита от чужих страниц: Origin должен совпадать с нашим адресом."""
    uid = await webchat.session_uid(request)
    if uid is None:
        return _html("<p>Войдите в кабинет, чтобы управлять заказом.</p>", status=401)
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return _html("<p>Запрос отклонён.</p>", status=403)
    user = await repo.get_user_by_tg(await canonical_tg(uid))
    oid = int(request.match_info["oid"])
    o = await orders.get_order_full(oid) if user else None
    if not o or o["user_id"] != user["id"]:
        return _html("<p>Заказ не найден.</p>", status=404)
    form = await request.post()
    action = form.get("action")
    actor = f"client:{user['tg_id']}"
    bot = request.app.get("bot")
    if action == "cancel":
        _, msg = await orders.client_cancel(bot, oid, user["id"], actor)
    elif action in ("problem", "move"):
        _, msg = await orders.client_request(bot, oid, action, form.get("text"), actor)
    else:
        return _html("<p>Неизвестное действие.</p>", status=400)
    raise web.HTTPFound(f"/cabinet?m={quote(msg)}")


async def page(request: web.Request) -> web.Response:
    uid = await webchat.session_uid(request)
    if uid is None:
        body = ("<h2>Кабинет</h2><p>Откройте кабинет из Telegram или по ссылке «Открыть на сайте (без VPN)» "
                "из бота.</p>")
        return _html(body, status=401)
    tg = await canonical_tg(uid)
    user = await repo.get_user_by_tg(tg)
    pets = await repo.list_pets(user["id"]) if user else []
    rows = await repo.user_orders(user["id"], limit=50) if user else []
    note = request.query.get("m")
    body = (f"<h2>Ваш кабинет</h2>{f'<p><b>{_e(note)}</b></p>' if note else ''}"
            f"<h3>Питомцы</h3>{_pets_html(pets)}"
            f"<h3>История заказов</h3>{await _orders_html(rows)}"
            f"<p><a href='/chat'>💬 Чат</a></p>")
    return _html(body)


def _html(body: str, status: int = 200) -> web.Response:
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{brand.NAME} — кабинет</title>{brand.HEAD}"
            "<body style='max-width:520px;margin:0 auto;padding:16px'>"
            f"{brand.backrow('/')}<div style='text-align:center;font-size:26px'>{brand.LOGO}</div>{brand.STRAP}{body}</body>")
    page = page.replace("<body style='max-width:520px", ribbons.html(ribbons.CLIENT) + "<body style='max-width:520px", 1)
    return web.Response(text=page, status=status, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def setup(app: web.Application) -> None:
    app.router.add_get("/cabinet", page)
    app.router.add_post("/cabinet/order/{oid}", order_action)
