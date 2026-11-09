"""Веб-заявка: страница /request для клиентов, у которых Telegram без VPN не открывается.

Страница без внешних ресурсов и без JavaScript (грузится на любой связи). Заявка сохраняется в web_requests,
админу сразу уходит сообщение в Telegram с телефоном: дальше он связывается с клиентом по телефону или СМС.
"""
import html as _html
import logging
import time

from aiohttp import web

import db
import orders
import pricing
import webchat
import brand
import ribbons
import talk
import texts
from config import config
from utils import fmt_local, normalize_phone, now_utc, rub

log = logging.getLogger(__name__)

RATE_LIMIT = 5          # заявок с одного адреса в час
RATE: dict[str, list[float]] = {}
MAX = {"name": 80, "address": 200, "when": 100, "pet": 120, "comment": 500}


def validate(form: dict) -> tuple[dict, dict]:
    """→ (очищенные значения, ошибки по полям). Ошибки — только про то, что клиент может исправить."""
    g = lambda k: str(form.get(k) or "").strip()  # noqa: E731
    clean = {k: g(k)[:n] for k, n in MAX.items()}
    clean["service_id"] = g("service_id")
    errors: dict[str, str] = {}
    if g("site"):  # ловушка для ботов: настоящий человек это поле не видит
        errors["bot"] = "spam"
    phone = normalize_phone(g("phone"))
    clean["phone"] = phone or g("phone")[:30]
    if not phone:
        errors["phone"] = "Проверьте номер: например, 8 900 123-45-67."
    if not clean["name"]:
        errors["name"] = "Напишите, как к вам обращаться."
    if not clean["address"]:
        errors["address"] = "Напишите адрес: улица и дом."
    if not clean["service_id"].isdigit():
        errors["service_id"] = "Выберите услугу."
    if not form.get("consent"):
        errors["consent"] = "Нужно согласие на обработку данных."
    return clean, errors


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


CSS = """
main{max-width:560px;margin:0 auto;padding:18px 16px 48px}h1{font-size:1.5rem;margin:.5em 0 .3em}
label{display:block;margin:14px 0 4px;font-weight:700}input,select,textarea{width:100%}textarea{min-height:84px}
.err{color:var(--err);font-size:.92rem;margin-top:4px}.hint{color:var(--mute);font-size:.95rem}
.check{display:flex;gap:10px;align-items:flex-start;font-weight:400}.check input{width:auto;margin-top:5px}
button{margin-top:22px;width:100%}.hp{position:absolute;left:-9999px}.top{display:flex;justify-content:center;font-size:1.6rem;margin:8px 0 6px}
"""


def render_form(services: list[dict], values: dict, errors: dict) -> str:
    v = lambda k: _e(values.get(k, ""))  # noqa: E731
    err = lambda k: f'<div class="err">{_e(errors[k])}</div>' if k in errors else ""  # noqa: E731
    groups = []
    for cat in texts.CATEGORY_ORDER:
        opts = [s for s in services if s["category"] == cat]
        if not opts:
            continue
        rows = "".join(
            f'<option value="{s["id"]}"{" selected" if str(s["id"]) == str(values.get("service_id")) else ""}>'
            f'{_e(texts.variant_label(cat, s["name"]))} — от {rub(s.get("from_price", s["base_price"]))}</option>' for s in opts)
        groups.append(f'<optgroup label="{_e(texts.CATEGORY_TITLES[cat])}">{rows}</optgroup>')
    checked = " checked" if values.get("consent") else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — заявка</title><meta name="description" content="Оставьте заявку на выгул, зоонаню, передержку или зоотакси в Москве и Московской области — подберём несколько вариантов.">
{brand.HEAD}<style>{CSS}</style></head><body>{ribbons.html(ribbons.CLIENT)}<main>
{brand.backrow("/")}
<div class="top">{brand.LOGO}</div>{brand.STRAP}
<h1>Заявка на услугу</h1>
<p class="hint">Работает без Telegram и без VPN. Мы позвоним или напишем СМС, уточним детали и сообщим точную цену.</p>
<form method="post" action="/request">
<label for="service_id">Услуга</label><select id="service_id" name="service_id"><option value="">Выберите…</option>{"".join(groups)}</select>{err("service_id")}
<label for="when">Когда нужно</label><input id="when" name="when" maxlength="100" value="{v("when")}" placeholder="например, завтра в 10:00">
<label for="address">Адрес</label><input id="address" name="address" maxlength="200" value="{v("address")}" placeholder="город, улица, дом, квартира">{err("address")}
<label for="pet">Питомец</label><input id="pet" name="pet" maxlength="120" value="{v("pet")}" placeholder="кличка, вид, порода">
<label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name">{err("name")}
<label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 900 123-45-67">{err("phone")}
<label for="comment">Комментарий</label><textarea id="comment" name="comment" maxlength="500">{v("comment")}</textarea>
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Отправить заявку</button></form></main></body></html>"""


def cancel_form(request_id: int) -> str:
    """Кнопка «Отменить заявку» для самого клиента (проверяется, что заявка из этого же браузера)."""
    if not request_id:
        return ""
    return (f'<form method="post" action="/request/cancel/{request_id}" onsubmit="return confirm(\'Отменить заявку №{request_id}?\')">'
            '<button class="btn ghost" type="submit">Отменить заявку</button></form>')


def render_done(request_id: int) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — заявка принята</title>
{brand.HEAD}<style>{CSS}</style></head><body>{ribbons.html(ribbons.CLIENT)}<main>{brand.backrow("/")}<div class="top">{brand.LOGO}</div>{brand.STRAP}<h1>✅ Заявка принята</h1>
<p>Номер заявки: <b>№{request_id}</b>. Мы свяжемся с вами по указанному телефону, уточним детали и сообщим цену.</p>
<p class="hint">Данные используются только для выполнения заявки. Удалить их можно, написав нам.</p>
<p><a class="btn" href="/chat">💬 Открыть чат с администратором</a></p>
<p class="hint">Администратор напишет вам в этот чат. Заходите с этого же телефона и браузера — мы вас узнаем.</p>
<p><a href="/request">Отправить ещё одну заявку</a></p>{cancel_form(request_id)}</main></body></html>"""


_client_ip = webchat.client_ip


def _limited(ip: str) -> bool:
    now = time.time()
    hits = [t for t in RATE.get(ip, []) if now - t < 3600]
    limited = len(hits) >= RATE_LIMIT
    if not limited:
        hits.append(now)
    RATE[ip] = hits
    return limited


async def _services() -> list[dict]:
    return await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1 ORDER BY id"))


async def get_form(request: web.Request) -> web.Response:
    return web.Response(text=render_form(await _services(), {}, {}), content_type="text/html",
                        headers={"Cache-Control": "no-cache"})


async def post_form(request: web.Request) -> web.Response:
    if _limited(_client_ip(request)):
        return web.Response(status=429, text="Слишком много заявок. Попробуйте через час или позвоните нам.")
    form = dict(await request.post())
    clean, errors = validate(form)
    clean["consent"] = bool(form.get("consent"))
    services = await _services()
    if clean["service_id"].isdigit() and not any(str(s["id"]) == clean["service_id"] for s in services):
        errors["service_id"] = "Выберите услугу."
    if "bot" in errors:
        return web.Response(text=render_done(0), content_type="text/html")  # боту делаем вид, что приняли
    if errors:
        return web.Response(status=400, text=render_form(services, clean, errors), content_type="text/html")
    svc = next(s for s in services if str(s["id"]) == clean["service_id"])
    # кто это: тот же браузер (cookie) остаётся тем же клиентом, новому браузеру выдаём свою гостевую сессию
    ip, device = _client_ip(request), webchat.device_label(request.headers.get("User-Agent"))
    web_uid, new_token = await webchat.session_uid(request), None
    if web_uid is None:
        new_token = await webchat.new_session(device=device, ip=ip)
        web_uid = (await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(new_token),)))["uid"]
    async with db.transaction() as conn:
        await conn.execute("INSERT OR IGNORE INTO users (tg_id, name, phone) VALUES (?,?,?)",
                           (web_uid, clean["name"], clean["phone"]))
        await conn.execute("UPDATE users SET phone=COALESCE(phone, ?), name=COALESCE(name, ?) WHERE tg_id=?",
                           (clean["phone"], clean["name"], web_uid))
        cur = await conn.execute(
            """INSERT INTO web_requests (service_id, name, phone, address, when_text, pet, comment, web_uid, ip, device)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (svc["id"], clean["name"], clean["phone"], clean["address"], clean["when"],
             clean["pet"], clean["comment"], web_uid, ip, device))
        rid = cur.lastrowid
    text = "\n".join(filter(None, [
        f"🌐 Заявка с сайта №{rid}",
        f"Услуга: {svc['name']} (от {rub(svc['base_price'])})",
        f"Имя: {clean['name']}", f"Телефон: {clean['phone']}",
        f"Зарегистрирована: {fmt_local(now_utc())}",
        f"Когда: {clean['when']}" if clean["when"] else "",
        f"Адрес: {clean['address']}",
        f"Питомец: {clean['pet']}" if clean["pet"] else "",
        f"Комментарий: {clean['comment']}" if clean["comment"] else "",
        "Свяжитесь с клиентом по телефону или напишите ему в чат (он увидит ответ на сайте):",
        f"{config.webapp_url}/portal/chat/{web_uid}"]))
    try:
        await talk.save(web_uid, "client", "in", text)
        await orders.notify(request.app["bot"], web_uid, f"✅ Заявка №{rid} принята. Администратор свяжется с вами и ответит здесь, в чате.")
        await orders.notify_admins(request.app["bot"], text)
    except Exception:
        log.exception("Не удалось уведомить админа о заявке с сайта №%s", rid)
    resp = web.Response(text=render_done(rid), content_type="text/html", headers={"Cache-Control": "no-cache"})
    if new_token:
        webchat._set_cookie(request, resp, new_token)
    return resp


async def post_cancel(request: web.Request) -> web.Response:
    """Клиент отменяет свою заявку: статус «Отменена клиентом», админ получает уведомление (Telegram, пуш, письмо)."""
    from urllib.parse import quote, urlparse
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.host:
        return web.Response(status=403, text="Запрос отклонён.")
    uid = await webchat.session_uid(request)
    if uid is None:
        return web.Response(status=401, text="Откройте сайт с того же телефона и браузера, где отправляли заявку.")
    try:
        rid = int(request.match_info["rid"])
    except ValueError:
        return web.Response(status=404)
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"UPDATE web_requests SET status='rejected', admin_note=?, {db.touch()} "
            "WHERE id=? AND web_uid=? AND status IN ('new','work')", ("Отменена клиентом", rid, uid))
        changed = cur.rowcount == 1
    if changed:
        row = await db.fetch_one("SELECT r.*, s.name AS service FROM web_requests r LEFT JOIN services s ON s.id=r.service_id WHERE r.id=?", (rid,))
        try:
            await orders.notify_admins(request.app["bot"], "\n".join(filter(None, [
                f"❌ Клиент {row['name']} отменил заявку №{rid}",
                f"Услуга: {row['service']}" if row["service"] else "", f"Телефон: {row['phone']}",
                f"Когда: {row['when_text']}" if row["when_text"] else ""])))
            await orders.notify(request.app["bot"], uid, f"❌ Заявка №{rid} отменена. Если передумаете, оставьте новую.")
        except Exception:
            log.exception("Не удалось уведомить админа об отмене заявки №%s", rid)
        msg = f"Заявка №{rid} отменена."
    else:
        row = await db.fetch_one("SELECT status, web_uid FROM web_requests WHERE id=?", (rid,))
        if not row or row["web_uid"] != uid:
            return web.Response(status=404, text="Заявка не найдена.")
        msg = "Эту заявку уже нельзя отменить самостоятельно — напишите нам в чат."
    raise web.HTTPFound(f"/cabinet?m={quote(msg)}")
