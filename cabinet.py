"""Личный кабинет клиента на сайте: питомцы и история заказов.

Один клиент — один кабинет: если веб-гость привязан к Telegram (users.verified_tg_id),
кабинет тот же, что у клиента в Telegram. Иначе — кабинет самой сессии (гость видит пустой)."""
import html as _h

from aiohttp import web

import brand
import repo
import texts
import webchat
from utils import fmt_local, rub


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


def _orders_html(rows: list[dict]) -> str:
    if not rows:
        return "<p class=muted>Заказов пока нет.</p>"
    items = []
    for o in rows:
        pet = f" · {o['pet_name']}" if o.get("pet_name") else ""
        status = texts.STATUS_LABELS.get(o["status"], o["status"])
        items.append(f"<li>№{_e(o['id'])} · {_e(o['name'])}{_e(pet)} · {_e(fmt_local(o['start_at']))}"
                     f" · {_e(rub(o['price_total']))}<br><span class=muted>{_e(status)}</span></li>")
    return "<ul>" + "".join(items) + "</ul>"


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
    body = (f"<h2>Ваш кабинет</h2>"
            f"<h3>Питомцы</h3>{_pets_html(pets)}"
            f"<h3>История заказов</h3>{_orders_html(rows)}"
            f"<p><a href='/chat'>💬 Чат</a></p>")
    return _html(body)


def _html(body: str, status: int = 200) -> web.Response:
    page = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{brand.NAME} — кабинет</title>{brand.HEAD}"
            "<body style='max-width:520px;margin:0 auto;padding:16px'>"
            f"<div style='text-align:center;font-size:26px'>{brand.LOGO}</div>{body}</body>")
    return web.Response(text=page, status=status, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def setup(app: web.Application) -> None:
    app.router.add_get("/cabinet", page)
