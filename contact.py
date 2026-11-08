"""Страница /contact: связаться с администратором сразу, без входа и меню.

Три способа на одном экране: написать в Telegram, позвонить (если задан ADMIN_PHONE), попросить перезвонить или оставить
сообщение (имя + телефон). Заявка попадает в «Заявки» портала, админу сразу уходит сообщение в Telegram.
Без JavaScript и внешних ресурсов — открывается на любой связи.
"""
import html as _html
import logging
import os

from aiohttp import web

import brand
import db
import orders
import ribbons
import webchat
import webform
from config import config
from utils import normalize_phone

log = logging.getLogger(__name__)

ADMIN_PHONE = os.getenv("ADMIN_PHONE", "")   # телефон администратора для кнопки «Позвонить»; без него кнопки нет
MAX_MESSAGE = 500

CSS = webform.CSS + """
.ways{display:grid;gap:10px;margin:16px 0 8px}.way{display:flex;align-items:center;gap:12px;padding:14px 16px;border-radius:999px;
text-decoration:none;font-weight:700;min-height:56px}.way small{display:block;font-weight:400;opacity:.85}
.way.tg{background:var(--coral);color:#fff}.way.tel{background:#fff;color:var(--coral);border:2px solid var(--coral)}
.or{text-align:center;color:var(--mute);margin:18px 0 0}
"""


def _e(v) -> str:
    return _html.escape(str(v or ""), quote=True)


def _tel() -> str:
    return normalize_phone(ADMIN_PHONE) if ADMIN_PHONE else ""


def render_form(values: dict, errors: dict) -> str:
    v = lambda k: _e(values.get(k, ""))  # noqa: E731
    err = lambda k: f'<div class="err">{_e(errors[k])}</div>' if k in errors else ""  # noqa: E731
    checked = " checked" if values.get("consent") else ""
    call = ('<a class="way tel" href="/contact/call" rel="nofollow"><span>📞</span><span>Позвонить администратору'
            '<small>обычный звонок по сотовой сети</small></span></a>') if _tel() else ""   # номера на странице нет: он отдаётся только при нажатии
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — связаться с администратором</title><meta name="description" content="Свяжитесь с администратором Зооконсьержа: поможем с выгулом, зооняней, передержкой и зоотакси в Химках и Москве.">
{brand.HEAD}<style>{CSS}</style></head><body>{ribbons.html(ribbons.CLIENT)}<main>
{brand.backrow("/")}
<div class="top">{brand.LOGO}</div>{brand.STRAP}
<h1>Написать администратору</h1>
<p class="hint">Выберите, как вам удобнее. Отвечаем в рабочее время.</p>
<div class="ways">
<a class="way tg" href="https://t.me/{_e(config.bot_username)}"><span>✈️</span><span>Написать в Telegram<small>ответ придёт туда же</small></span></a>
{call}
</div>
<p class="or">или оставьте номер — перезвоним</p>
<form method="post" action="/contact">
<label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name">{err("name")}
<label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 900 123-45-67">{err("phone")}
<label for="message">Сообщение (необязательно)</label><textarea id="message" name="message" maxlength="{MAX_MESSAGE}">{v("message")}</textarea>
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Перезвоните мне</button></form></main></body></html>"""


def render_done() -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — принято</title>
{brand.HEAD}<style>{CSS}</style></head><body>{ribbons.html(ribbons.CLIENT)}<main>{brand.backrow("/")}<div class="top">{brand.LOGO}</div>{brand.STRAP}
<h1>✅ Сообщение принято</h1><p>Администратор получил его сразу и свяжется с вами по указанному телефону.</p>
<p><a class="btn" href="/">На главную</a></p></main></body></html>"""


async def get_page(request: web.Request) -> web.Response:
    return web.Response(text=render_form({}, {}), content_type="text/html", headers={"Cache-Control": "no-cache"})


async def call(request: web.Request) -> web.Response:
    """Нажатие «Позвонить»: номер отдаётся только сейчас, переходом на tel: — телефон звонит по сотовой сети."""
    tel = _tel()
    if not tel:
        return web.Response(status=404, text="Номер не задан.")
    return web.Response(status=302, headers={"Location": f"tel:{tel}", "Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


async def post_page(request: web.Request) -> web.Response:
    ip = webchat.client_ip(request)
    if webform._limited(ip):
        return web.Response(status=429, text="Слишком много обращений. Попробуйте через час или напишите нам в Telegram.")
    form = dict(await request.post())
    g = lambda k: str(form.get(k) or "").strip()  # noqa: E731
    if g("site"):
        return web.Response(text=render_done(), content_type="text/html")   # боту делаем вид, что приняли
    phone = normalize_phone(g("phone"))
    values = {"name": g("name")[:80], "phone": g("phone")[:30], "message": g("message")[:MAX_MESSAGE], "consent": bool(form.get("consent"))}
    errors = {}
    if not phone:
        errors["phone"] = "Проверьте номер: например, 8 900 123-45-67."
    if not values["name"]:
        errors["name"] = "Напишите, как к вам обращаться."
    if not values["consent"]:
        errors["consent"] = "Нужно согласие на обработку данных."
    if errors:
        return web.Response(status=400, text=render_form(values, errors), content_type="text/html")
    async with db.transaction() as conn:
        cur = await conn.execute(
            """INSERT INTO web_requests (service_id, name, phone, contact_pref, address, when_text, pet, comment, ip, device)
               VALUES (NULL,?,?,?,?,?,?,?,?,?)""",
            (values["name"], phone, "call", "Связь с администратором", None, None, values["message"], ip,
             webchat.device_label(request.headers.get("User-Agent"))))
        rid = cur.lastrowid
    text = "\n".join(filter(None, [f"📞 Просят связаться, заявка №{rid}", f"Имя: {values['name']}", f"Телефон: {phone}",
                                   f"Сообщение: {values['message']}" if values["message"] else "", "Перезвоните клиенту."]))
    try:
        await orders.notify_admins(request.app["bot"], text)
    except Exception:  # noqa: BLE001 — обращение уже записано, админ увидит его в портале
        log.exception("Не удалось уведомить админа об обращении №%s", rid)
    return web.Response(text=render_done(), content_type="text/html", headers={"Cache-Control": "no-cache"})
