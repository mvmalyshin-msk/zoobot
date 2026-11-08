"""Страница «Тарифы» в портале администратора: всё, что раньше меняли командами /price_set, /taxi_tariff, /rule_add.

Три блока: начальная стоимость каждой услуги, ставки зоотакси (км, минуты, животное, пробки) и наценки/надбавки
(в процентах или в рублях; «Наценка сервиса» — общий процент сверху на любую услугу). Меняется только то, что уже есть:
новые правила по-прежнему добавляются командой /rule_add. Старые заказы не пересчитываются."""
import logging
import re

from aiohttp import web

import db
import portal
import taxi

log = logging.getLogger(__name__)

MAX_PRICE = 1_000_000
MAX_PERCENT, MAX_FIXED = 300, 100_000
TAXI_LABELS = {"км": "₽ за километр", "мин": "₽ за минуту", "животное": "₽ за перевозку животного",
               "включено_км": "км входят в посадку", "включено_мин": "минут входят в посадку",
               "пробки": "поправка на пробки (во сколько раз дольше)"}
CONDITION_TITLES = {"always": "всегда, на любую услугу", "night": "ночь 22:00–07:00", "holiday": "праздничный день",
                    "urgent_today": "заказ на сегодня", "outside_mkad": "за МКАД", "pet_size_l": "крупная собака",
                    "pet_danger": "сложный характер питомца", "pet_notes": "особенности здоровья", "weekend": "выходные",
                    "pet_other": "необычный питомец"}


def _num(text, low: float, high: float) -> float | None:
    try:
        n = float(str(text).strip().replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    return n if low <= n <= high and n == n else None


def _form(step: str, fields: str, button: str = "Сохранить") -> str:
    return (f'<form method="post" action="/portal/tariffs" class="trow"><input type="hidden" name="step" value="{step}">'
            f'{fields}<button class="btn small">{button}</button></form>')


async def _body() -> str:
    e = portal._e
    services = await db.fetch_all("SELECT * FROM services ORDER BY id")
    rows = "".join(
        _form("price", f'<input type="hidden" name="id" value="{s["id"]}"><span class="tl">{e(s["name"])}'
                       f'<small class="muted"> · за {e(s["unit"])}{"" if s["active"] else " · отключена"}</small></span>'
                       f'<span class="tin"><input name="price" inputmode="numeric" value="{s["base_price"]}" aria-label="Цена, ₽"> ₽</span>')
        for s in services)
    t = await taxi.get_tariff()
    taxi_rows = "".join(
        _form("taxi", f'<input type="hidden" name="name" value="{e(name)}"><span class="tl">{e(TAXI_LABELS[name])}</span>'
                      f'<span class="tin"><input name="value" inputmode="decimal" value="{getattr(t, field):g}" aria-label="{e(name)}"></span>')
        for name, (field, _, _) in taxi.PARAMS.items())
    rules = await db.fetch_all(
        "SELECT r.*, s.name AS sname FROM price_rules r LEFT JOIN services s ON s.id=r.service_id "
        "ORDER BY CASE r.condition_type WHEN 'always' THEN 0 ELSE 1 END, r.id")
    rule_rows = ""
    for r in rules:
        unit = "%" if r["effect_type"] == "percent" else "₽"
        where = r["sname"] or "все услуги"
        rule_rows += _form(
            "rule", f'<input type="hidden" name="id" value="{r["id"]}"><span class="tl">{e(r["name"])}'
                    f'<small class="muted"> · {e(CONDITION_TITLES.get(r["condition_type"], r["condition_type"]))} · {e(where)}</small></span>'
                    f'<span class="tin"><input name="value" inputmode="decimal" value="{r["value"]:g}" aria-label="Размер, {unit}"> {unit}</span>'
                    f'<label class="on"><input type="checkbox" name="active" value="1"{" checked" if r["active"] else ""}> действует</label>')
    return (f'<h1>Тарифы</h1>'
            f'<p class="muted">Меняйте цифры и нажимайте «Сохранить» у нужной строки. Новые цены сразу видят клиенты; '
            f'уже оформленные заказы не пересчитываются.</p>'
            f'<h2>Начальная стоимость услуг</h2><div class="card tcard">{rows}</div>'
            f'<h2>Ставки зоотакси</h2><p class="muted">Цена поездки = начальная стоимость (посадка) + километры и минуты '
            f'сверх включённых + животное. Посадка меняется в блоке выше.</p><div class="card tcard">{taxi_rows}</div>'
            f'<h2>Наценки и надбавки</h2><p class="muted">«Наценка сервиса» — общий процент, он добавляется к цене любой услуги '
            f'(0 — наценки нет). Остальные включаются, когда подходит условие (ночь, срочно и т. д.). '
            f'Новое правило добавляется командой /rule_add в боте.</p><div class="card tcard">{rule_rows}</div>')


EXTRA_CSS = (".trow{display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;padding:12px 0;border-top:1px solid var(--line)}"
             ".trow:first-child{border-top:0}.tl{flex:1 1 100%}.tl small{display:block}.tin{flex:1 1 auto;white-space:nowrap}"
             ".tin input{width:120px}.trow .btn{margin-left:auto}.trow label.on{display:inline-flex;gap:6px;align-items:center;"
             "font-weight:400;margin:0;flex:1 1 auto}.tcard{padding:4px 14px}")


@portal.admin_page
async def tariffs_page(request: web.Request, uid: int) -> web.Response:
    page = portal.page("Тарифы", await _body(), "tariffs", request.query.get("m"))
    return portal._html_resp(page.replace("</style>", EXTRA_CSS + "</style>", 1))


@portal.admin_page
async def tariffs_action(request: web.Request, uid: int) -> web.Response:
    form = await request.post()
    step, back = form.get("step"), "/portal/tariffs"
    rid = re.sub(r"\D", "", str(form.get("id", "")))[:12]
    if step == "price":
        price = _num(form.get("price", ""), 1, MAX_PRICE)
        s = await db.fetch_one("SELECT * FROM services WHERE id=?", (int(rid),)) if rid else None
        if s is None or price is None or price != int(price):
            return portal._redirect(back, f"Цена: целое число рублей от 1 до {MAX_PRICE:,}.".replace(",", " "))
        await db.execute(f"UPDATE services SET base_price=?, {db.touch()} WHERE id=?", (int(price), s["id"]))
        log.info("Тариф: админ %s поставил «%s» %s ₽ (было %s)", uid, s["name"], int(price), s["base_price"])
        return portal._redirect(back, f"✅ {s['name']}: {s['base_price']} → {int(price)} ₽")
    if step == "taxi":
        try:
            await taxi.set_param(str(form.get("name", "")), str(form.get("value", "")))
        except ValueError as exc:
            return portal._redirect(back, f"⚠️ {exc}")
        log.info("Тариф: админ %s изменил ставку такси %s=%s", uid, form.get("name"), form.get("value"))
        return portal._redirect(back, "✅ Ставка зоотакси сохранена.")
    if step == "rule":
        r = await db.fetch_one("SELECT * FROM price_rules WHERE id=?", (int(rid),)) if rid else None
        high = MAX_PERCENT if r and r["effect_type"] == "percent" else MAX_FIXED
        value = _num(form.get("value", ""), 0, high)
        if r is None or value is None:
            return portal._redirect(back, f"Размер: число от 0 до {high}.")
        active = 1 if form.get("active") else 0
        await db.execute(f"UPDATE price_rules SET value=?, active=?, {db.touch()} WHERE id=?", (value, active, r["id"]))
        log.info("Тариф: админ %s правило «%s» = %s, действует=%s", uid, r["name"], value, active)
        return portal._redirect(back, f"✅ {r['name']}: {value:g}{'%' if r['effect_type'] == 'percent' else ' ₽'}"
                                      f"{'' if active else ' (выключено)'}")
    return portal._redirect(back)


def setup(app: web.Application) -> None:
    app.router.add_get("/portal/tariffs", tariffs_page)
    app.router.add_post("/portal/tariffs", tariffs_action)
