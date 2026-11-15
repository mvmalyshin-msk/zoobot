"""Веб-заявка: страница /request для клиентов, у которых Telegram без VPN не открывается.

Страница без внешних ресурсов и без JavaScript (грузится на любой связи). Заявка сохраняется в web_requests,
админу сразу уходит сообщение в Telegram с телефоном: дальше он связывается с клиентом по телефону или СМС.
"""
import html as _html
import logging
import secrets
import time
from datetime import date, datetime

from aiohttp import web

import db
import geo
import orders
import pricing
import webchat
import brand
import talk
import texts
from config import config
from utils import fmt_local, normalize_phone, now_utc, rub

log = logging.getLogger(__name__)

RATE_LIMIT = 5          # заявок с одного адреса в час
RATE: dict[str, list[float]] = {}
MAX = {"name": 80, "address": 200, "when": 100, "pet": 120, "comment": 500, "dest": 200}
SEEN: dict[str, tuple[float, int]] = {}   # одноразовый ключ формы → (когда, №заявки): двойное нажатие не создаёт вторую
# своя заявка для каждой услуги (вариант A проектного предложения 15.11.2026)
SERVICE_TITLE = {"walk": "выгул", "sitter": "зооняню", "boarding": "передержку", "taxi": "зоотакси"}
SERVICE_HINT = {
    "walk": "Выберите длительность и время: подберём выгульщика рядом с вами.",
    "sitter": "Няня приходит к питомцу домой или живёт с ним, пока вас нет.",
    "boarding": "Питомец живёт у ситтера дома или в зоогостинице, пока вас нет.",
    "taxi": "Отвезём питомца к ветеринару, грумеру, на передержку или домой.",
}


def _today() -> date:
    return datetime.now(config.tz).date()


def _iso(v: str):
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _when(cat: str, form: dict, errors: dict) -> str:
    """Дата из календаря → текст для админа: «16.11.2026, 10:00» или «с 16.11.2026 по 20.11.2026»."""
    g = lambda k: str(form.get(k) or "").strip()  # noqa: E731
    d = _iso(g("when_date"))
    if not d:
        errors["when_date"] = "Выберите дату."
        return ""
    if d < _today():
        errors["when_date"] = "Дата уже прошла — выберите сегодня или позже."
        return ""
    if cat == "boarding":
        to = _iso(g("when_to"))
        if to and to < d:
            errors["when_to"] = "Дата «по» раньше даты «с»."
            return ""
        return f"с {d:%d.%m.%Y}" + (f" по {to:%d.%m.%Y}" if to else "")
    t = g("when_time")[:5]
    return f"{d:%d.%m.%Y}" + (f", {t}" if len(t) == 5 and t[2] == ":" and t.replace(":", "").isdigit() else "")


def validate(form: dict) -> tuple[dict, dict]:
    """→ (очищенные значения, ошибки по полям). Ошибки — только про то, что клиент может исправить."""
    g = lambda k: str(form.get(k) or "").strip()  # noqa: E731
    clean = {k: g(k)[:n] for k, n in MAX.items()}
    clean["service_id"] = g("service_id")
    clean["address_ok"], clean["dest_ok"] = g("address_ok")[:200], g("dest_ok")[:200]  # адрес, выбранный из подсказок
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
    cat = g("service")
    clean["service"] = cat if cat in SERVICE_TITLE else ""
    for k in ("when_date", "when_to", "when_time"):
        clean[k] = g(k)[:10]
    if clean["service"]:
        clean["when"] = _when(cat, form, errors)
        if cat == "taxi" and not clean["dest"]:
            errors["dest"] = "Напишите, куда отвезти питомца."
        elif cat == "taxi" and geo.same_place(clean["address"], clean["dest"]):
            errors["dest"] = "Адреса «Откуда» и «Куда» совпадают. Напишите, куда отвезти питомца."
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
{brand.HEAD}<style>{CSS}</style></head><body>{brand.public_header()}<main class="public-main public-form">
<p class="eyebrow">ЗАЯВКА · МОСКВА И МОСКОВСКАЯ ОБЛАСТЬ</p><h1>Подберём услугу для питомца</h1>
<p class="hint form-intro">Расскажите о задаче. Мы уточним детали и сообщим стоимость до подтверждения.</p>
<form method="post" action="/request">
<div class="form-grid">
<div class="field"><label for="service_id">Услуга</label><select id="service_id" name="service_id"><option value="">Выберите…</option>{"".join(groups)}</select>{err("service_id")}</div>
<div class="field"><label for="when">Когда нужно</label><input id="when" name="when" maxlength="100" value="{v("when")}" placeholder="например, завтра в 10:00"></div>
<div class="field field-wide"><label for="address">Адрес</label><input id="address" name="address" maxlength="200" value="{v("address")}" placeholder="например, Химки, улица Победы, 3, кв. 5">{err("address")}</div>
<div class="field"><label for="pet">Питомец</label><input id="pet" name="pet" maxlength="120" value="{v("pet")}" placeholder="например, Рекс, собака, лабрадор, 3 года"></div>
<div class="field"><label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name" placeholder="например, Анна">{err("name")}</div>
<div class="field"><label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 900 123-45-67">{err("phone")}</div>
<div class="field field-wide"><label for="comment">Комментарий</label><textarea id="comment" name="comment" maxlength="500">{v("comment")}</textarea></div>
</div>
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Отправить заявку</button><p class="form-note">Нажимая кнопку, вы отправляете заявку на подбор услуги. Это не обязывает вас подтверждать заказ.</p></form>{LIVE_JS}</main>{brand.public_footer()}</body></html>"""


SERVICE_CSS = """
.variants{display:grid;gap:8px;margin:6px 0 0}.variant{display:flex;align-items:center;gap:10px;font-weight:400;margin:0;
padding:12px 14px;border:1.5px solid var(--line,#ddd);border-radius:12px;cursor:pointer}.variant input{width:auto;margin:0}
.variant b{margin-left:auto;white-space:nowrap}.variant input{accent-color:var(--coral)}.variant:has(input:checked){border-color:var(--coral);background:var(--peach)}
.step{font-size:1.05rem;margin:22px 0 2px}.row2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}.row2>div{min-width:0}.row2 input{min-width:0;max-width:100%;box-sizing:border-box}.row2 label{margin-top:8px}
.other{font-size:.95rem}
.sug-box{display:grid;gap:4px;margin:4px 0}.sug-box:empty{display:none}
.sug{display:block;width:100%;text-align:left;font:inherit;padding:10px 12px;border:1.5px solid var(--line,#ddd);border-radius:10px;background:#fff;cursor:pointer}
.sug:hover,.sug:focus{border-color:var(--coral);background:var(--peach)}.sug-note{font-size:.9rem;color:#777;padding:4px 2px}
"""

LIVE_JS = """<script>(function(){
var f=document.querySelector('main form');if(!f)return;f.noValidate=true;
var M={phone:'Проверьте номер: например, 8 900 123-45-67.',name:'Напишите, как к вам обращаться.',
address:'Напишите адрес: улица и дом.',house:'Укажите номер дома: без него водитель не найдёт место. Например: Ленина улица, 5.',
dest:'Напишите, куда отвезти питомца.',same:'Адреса «Откуда» и «Куда» совпадают. Напишите, куда отвезти питомца.',
past:'Дата уже прошла — выберите сегодня или позже.',to:'Дата «по» раньше даты «с».',
consent:'Нужно согласие на обработку данных.',service_id:'Выберите услугу.'};
function val(n){var e=f.elements[n];return e?String(e.value||'').trim():''}
function phoneOk(t){var d=t.replace(/\\D/g,''),n;if(d.length==11&&'78'.indexOf(d[0])>=0)n=d.slice(1);else if(d.length==10)n=d;else return false;
 if(/^(\\d)\\1+$/.test(n))return false;var a='01234567890123456789',b='98765432109876543210';return a.indexOf(n)<0&&b.indexOf(n)<0}
function taxi(){return val('service')=='taxi'}
var R={
phone:function(){return phoneOk(val('phone'))?'':M.phone},
name:function(){return val('name')?'':M.name},
address:function(){var a=val('address');if(!a)return M.address;if(taxi()&&!/\\d/.test(a.replace(/(,|\\s)*(кв|квартира|офис|оф)\\.?\\s*\\d+.*$/i,'')))return M.house;return ''},
dest:function(){if(!taxi())return '';var d=val('dest');if(!d)return M.dest;if(d.toLowerCase()==val('address').toLowerCase())return M.same;
 if(!/\\d/.test(d))return M.house;return ''},
when_date:function(){var e=f.elements.when_date;if(!e||!e.value)return '';return e.min&&e.value<e.min?M.past:''},
when_to:function(){var e=f.elements.when_to,a=f.elements.when_date;return e&&a&&e.value&&a.value&&e.value<a.value?M.to:''},
service_id:function(){var e=f.elements.service_id;return e&&e.tagName=='SELECT'&&!e.value?M.service_id:''},
consent:function(){var e=f.elements.consent;return e&&!e.checked?M.consent:''}};
var touched={};
function show(n){var e=f.elements[n];if(!e||!e.setAttribute||!R[n])return '';var m=R[n]();var host=(e.type=='checkbox'?e.closest('label'):e);
 var box=f.querySelector('[data-live-for="'+n+'"]');
 var old=host.nextElementSibling;if(old&&old.classList.contains('err')&&!old.hasAttribute('data-live-for'))old.remove();
 if(!box){box=document.createElement('div');box.className='err';box.setAttribute('data-live-for',n);box.setAttribute('role','alert');host.insertAdjacentElement('afterend',box)}
 box.textContent=m;box.hidden=!m;e.setAttribute('aria-invalid',m?'true':'false');if(e.style)e.style.borderColor=m?'var(--err,#c0392b)':'';return m}
Object.keys(R).forEach(function(n){var e=f.elements[n];if(!e||!e.addEventListener)return;
 var ev=e.type=='checkbox'||e.tagName=='SELECT'?'change':'blur';
 e.addEventListener(ev,function(){touched[n]=1;show(n)});
 e.addEventListener('input',function(){if(touched[n])show(n)});
 e.addEventListener('change',function(){touched[n]=1;show(n)})});
f.addEventListener('submit',function(ev){var first=null;
 Object.keys(R).forEach(function(n){if(!f.elements[n])return;touched[n]=1;if(show(n)&&!first)first=f.elements[n]});
 if(first){ev.preventDefault();ev.stopImmediatePropagation();first.scrollIntoView({block:'center'});first.focus()}},true);
})();</script>"""

ADDR_JS = """<script>(function(){
var BBOX=[35.1,54.2,40.3,56.97],STATES=['москва','московская область','moscow','moscow oblast'];
function label(p){var street=p.street||p.name||'',parts=[street,p.housenumber,p.district||p.suburb,p.city||p.town||p.village||p.locality||p.county],seen=[];
parts.forEach(function(x){if(x&&seen.indexOf(x)<0)seen.push(x)});return seen.join(', ')}
function photon(q){var url='https://photon.komoot.io/api/?limit=10&lang=default&lat=55.75&lon=37.62&bbox='+BBOX.join(',')+'&q='+encodeURIComponent(q);
return fetch(url).then(function(r){return r.json()}).then(function(j){var items=[],street=false;
(j.features||[]).forEach(function(f){var p=f.properties||{},c=f.geometry&&f.geometry.coordinates;
if(!c||c[0]<BBOX[0]||c[0]>BBOX[2]||c[1]<BBOX[1]||c[1]>BBOX[3])return;
if(p.state&&STATES.indexOf(String(p.state).toLowerCase())<0)return;
if(!p.housenumber){street=true;return}var l=label(p);if(l&&items.indexOf(l)<0)items.push(l)});
return{items:items.slice(0,5),street_only:street&&!items.length}})}
function server(q){return fetch('/api/addr?q='+encodeURIComponent(q)).then(function(r){return r.json()})}
function attach(id){var inp=document.getElementById(id),ok=document.querySelector('input[name='+id+'_ok]'),box=document.getElementById(id+'-sug'),t,last='';
if(!inp||!box)return;
function note(text){var n=document.createElement('div');n.className='sug-note';n.textContent=text;box.appendChild(n)}
function show(d){box.innerHTML='';(d.items||[]).forEach(function(it){var b=document.createElement('button');b.type='button';b.className='sug';
b.setAttribute('data-for',id);b.setAttribute('data-pick',it);b.textContent=it;box.appendChild(b)});
if(!(d.items||[]).length&&!d.down){note(d.street_only?'Нашёл улицу. Допишите номер дома, например: Ленина 5.':'Не нашёл такой адрес. Проверьте написание, добавьте город и номер дома.')}}
inp.addEventListener('input',function(){ok.value='';clearTimeout(t);var q=inp.value.trim();
if(q.length<4){box.innerHTML='';return}
t=setTimeout(function(){if(q===last)return;last=q;
photon(q).catch(function(){return server(q)}).then(show).catch(function(){})},400)})}
attach('address');attach('dest');
document.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('.sug');if(!b)return;
var id=b.getAttribute('data-for'),v=b.getAttribute('data-pick');document.getElementById(id).value=v;
document.querySelector('input[name='+id+'_ok]').value=v;document.getElementById(id+'-sug').innerHTML=''});
})();</script>"""


def render_service_form(cat: str, variants: list[dict], values: dict, errors: dict, nonce: str) -> str:
    """Заявка на одну услугу: 1) вариант с ценой, 2) когда, 3) где, 4) питомец и контакты."""
    v = lambda k: _e(values.get(k, ""))  # noqa: E731
    err = lambda k: f'<div class="err">{_e(errors[k])}</div>' if k in errors else ""  # noqa: E731
    chosen = str(values.get("service_id") or min(variants, key=lambda s: s["from_price"])["id"])
    cards = "".join(
        f'<label class="variant"><input type="radio" name="service_id" value="{s["id"]}"'
        f'{" checked" if str(s["id"]) == chosen else ""}>'
        f'<span>{_e(texts.variant_label(cat, s["name"]))}</span><b>от {rub(s["from_price"])}</b></label>'
        for s in sorted(variants, key=lambda s: s["from_price"]))
    today = _today().isoformat()
    if cat == "boarding":
        when = (f'<div class="row2"><div><label for="when_date">С</label><input id="when_date" name="when_date" type="date" min="{today}" value="{v("when_date")}">{err("when_date")}</div>'
                f'<div><label for="when_to">По</label><input id="when_to" name="when_to" type="date" min="{today}" value="{v("when_to")}">{err("when_to")}</div></div>')
    else:
        when = (f'<div class="row2"><div><label for="when_date">Дата</label><input id="when_date" name="when_date" type="date" min="{today}" value="{v("when_date")}">{err("when_date")}</div>'
                f'<div><label for="when_time">Время</label><input id="when_time" name="when_time" type="time" step="900" value="{v("when_time")}"></div></div>')
    if cat == "taxi":
        opts = lambda k: "".join(  # noqa: E731
            f'<button type="button" class="sug" data-for="{k}" data-pick="{_e(o)}">{_e(o)}</button>'
            for o in (values.get(k + "_opts") or []))
        where = (f'<label for="address">Откуда</label><input id="address" name="address" maxlength="200" value="{v("address")}" placeholder="например, Москва, Тверская улица, 12" autocomplete="off">'
                 f'<input type="hidden" name="address_ok" value="{v("address_ok")}">{err("address")}<div class="sug-box" id="address-sug">{opts("address")}</div>'
                 f'<label for="dest">Куда</label><input id="dest" name="dest" maxlength="200" value="{v("dest")}" placeholder="например, Москва, Ленинский проспект, 10" autocomplete="off">'
                 f'<input type="hidden" name="dest_ok" value="{v("dest_ok")}">{err("dest")}<div class="sug-box" id="dest-sug">{opts("dest")}</div>'
                 f'<p class="hint">Начните писать адрес и выберите нужный из списка: так машина приедет точно.</p>{ADDR_JS}')
    else:
        label = "Ваш район или адрес" if cat == "boarding" else "Адрес"
        hint = "подберём передержку поближе" if cat == "boarding" else "например, Химки, улица Победы, 3, кв. 5"
        where = f'<label for="address">{label}</label><input id="address" name="address" maxlength="200" value="{v("address")}" placeholder="{hint}">{err("address")}'
    checked = " checked" if values.get("consent") else ""
    title = SERVICE_TITLE[cat]
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — заявка на {title}</title><meta name="description" content="Заявка на {title} в Москве и Московской области: цена от {rub(min(s['from_price'] for s in variants))}.">
{brand.HEAD}<style>{CSS}{SERVICE_CSS}</style></head><body>{brand.public_header()}<main class="public-main public-form">
<h1>Заявка на {title}</h1><p class="hint form-intro">{SERVICE_HINT[cat]} Точную цену подтвердим до заказа.</p>
<form method="post" action="/request"><input type="hidden" name="service" value="{cat}"><input type="hidden" name="nonce" value="{_e(nonce)}">
<h2 class="step">1. Вариант</h2><div class="variants">{cards}</div>{err("service_id")}
<p class="other"><a href="/request">Другая услуга</a></p>
<h2 class="step">2. Когда</h2>{when}
<h2 class="step">3. Где</h2>{where}
<h2 class="step">4. Питомец и контакты</h2>
<label for="pet">Питомец</label><input id="pet" name="pet" maxlength="120" value="{v("pet")}" placeholder="например, Рекс, собака, лабрадор, 3 года">
<div class="row2"><div><label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name" placeholder="например, Анна">{err("name")}</div>
<div><label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 900 123-45-67">{err("phone")}</div></div>
<label for="comment">Комментарий</label><textarea id="comment" name="comment" maxlength="500" placeholder="характер, привычки, что важно знать">{v("comment")}</textarea>
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Отправить заявку</button><p class="form-note">Это не заказ: администратор сначала подтвердит цену и исполнителя.</p></form>{LIVE_JS}</main>{brand.public_footer()}</body></html>"""


def _page(services: list[dict], values: dict, errors: dict) -> str:
    cat = values.get("service", "")
    variants = [s for s in services if s["category"] == cat]
    if cat in SERVICE_TITLE and variants:
        return render_service_form(cat, variants, values, errors, values.get("nonce") or secrets.token_urlsafe(12))
    return render_form(services, values, errors)


def cancel_form(request_id: int) -> str:
    """Кнопка «Отменить заявку» для самого клиента (проверяется, что заявка из этого же браузера)."""
    if not request_id:
        return ""
    return (f'<form method="post" action="/request/cancel/{request_id}" onsubmit="return confirm(\'Отменить заявку №{request_id}?\')">'
            '<button class="btn ghost" type="submit">Отменить заявку</button></form>')


def render_done(request_id: int) -> str:
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — заявка принята</title>
{brand.HEAD}<style>{CSS}</style></head><body>{brand.public_header()}<main class="public-main public-form"><h1>Заявка принята</h1>
<p>Номер заявки: <b>№{request_id}</b>. Администратор свяжется с вами в течение рабочего дня, обычно за 1–2 часа: уточнит детали и назовёт точную цену.</p>
<p class="hint">Данные используются только для выполнения заявки. Удалить их можно, написав нам.</p>
<p><a class="btn" href="/chat">💬 Открыть чат с администратором</a></p>
<p class="hint">Администратор напишет вам в этот чат. Заходите с этого же телефона и браузера — мы вас узнаем.</p>
<p><a href="/request">Отправить ещё одну заявку</a></p>{cancel_form(request_id)}</main>{brand.public_footer()}</body></html>"""


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


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


async def check_taxi_addresses(clean: dict, errors: dict) -> None:
    """Зоотакси: адрес с номером дома, найденный на карте. Выбран из подсказок или набран ровно как в подсказке — принимаем;
    без дома, не найден или не выбран — просим исправить, показывая варианты. Карта недоступна — клиента не блокируем,
    но номер дома требуем всё равно: улица без дома — не адрес."""
    for key in ("address", "dest"):
        text = clean.get(key) or ""
        if key in errors or not text:
            continue
        if not geo.has_house_number(text):
            errors[key] = "Укажите номер дома: без него водитель не найдёт место. Например: Ленина улица, 5."
            continue
        if clean.get(key + "_ok") == text:
            continue
        places = await geo.search(text)
        if places is None:
            continue
        if not places:
            errors[key] = "Не нашёл такой адрес на карте. Проверьте написание, добавьте город и номер дома."
            continue
        houses = [p for p in places if p.has_house]
        if not houses:
            errors[key] = "Нашёл улицу, но дома с таким номером на карте нет. Проверьте номер дома."
            continue
        if any(_norm(p.label) == _norm(text) for p in houses):
            clean[key + "_ok"] = text
            continue
        clean[key + "_opts"] = [p.label for p in houses[:5]]
        notice = (geo.house_notice(text, houses) or "").strip().removeprefix("⚠️ ")
        errors[key] = (notice + " " if notice else "") + "Выберите адрес из подсказок ниже."


_addr_hits: dict[str, list[float]] = {}


async def get_addr(request: web.Request) -> web.Response:
    """Подсказки адреса для формы зоотакси: до 5 настоящих адресов с карты. Пустой ответ с down — карта недоступна."""
    import portal
    q = " ".join(request.query.get("q", "").split())[:200]
    if portal._limit(_addr_hits, "a:" + _client_ip(request), 30, 60):
        return web.json_response({"items": []}, status=429)
    if len(q) < 5 or not geo.looks_like_address(q):
        return web.json_response({"items": []})
    places = await geo.search(q)
    if places is None:
        return web.json_response({"items": [], "down": True})
    houses = [p.label for p in places if p.has_house][:5]
    return web.json_response({"items": houses, "street_only": bool(places) and not houses})


async def get_form(request: web.Request) -> web.Response:
    """Форма заявки. С главной приходят с ?service=walk — тогда эта услуга (самый доступный вариант) уже выбрана."""
    services = await _services()
    cat = request.query.get("service", "")
    values = {"service": cat, "nonce": secrets.token_urlsafe(12)} if cat in SERVICE_TITLE else {}
    return web.Response(text=_page(services, values, {}), content_type="text/html",
                        headers={"Cache-Control": "no-cache"})


async def post_form(request: web.Request) -> web.Response:
    if _limited(_client_ip(request)):
        return web.Response(status=429, text="Слишком много заявок. Попробуйте через час или позвоните нам.")
    form = dict(await request.post())
    clean, errors = validate(form)
    clean["consent"] = bool(form.get("consent"))
    clean["nonce"] = str(form.get("nonce") or "")[:40]
    now = time.time()
    for k in [k for k, (t, _) in SEEN.items() if now - t > 3600]:
        SEEN.pop(k, None)
    if clean["nonce"] in SEEN:                                  # второе нажатие той же формы
        return web.Response(text=render_done(SEEN[clean["nonce"]][1]), content_type="text/html",
                            headers={"Cache-Control": "no-cache"})
    services = await _services()
    if clean["service_id"].isdigit() and not any(str(s["id"]) == clean["service_id"] for s in services):
        errors["service_id"] = "Выберите услугу."
    if "bot" in errors:
        return web.Response(text=render_done(0), content_type="text/html")  # боту делаем вид, что приняли
    if clean["service"] == "taxi" and "bot" not in errors:
        await check_taxi_addresses(clean, errors)
    if errors:
        return web.Response(status=400, text=_page(services, clean, errors), content_type="text/html")
    svc = next(s for s in services if str(s["id"]) == clean["service_id"])
    taxi = svc["category"] == "taxi" and clean["dest"]
    if taxi:
        clean["address"] = f"{clean['address']} → {clean['dest']}"[:400]
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
    if clean["nonce"]:
        SEEN[clean["nonce"]] = (now, rid)
    text = "\n".join(filter(None, [
        f"🌐 Заявка с сайта №{rid}",
        f"Услуга: {svc['name']} (от {rub(svc.get('from_price', svc['base_price']))} для клиента)",
        f"Имя: {clean['name']}", f"Телефон: {clean['phone']}",
        f"Зарегистрирована: {fmt_local(now_utc())}",
        f"Когда: {clean['when']}" if clean["when"] else "",
        f"{'Маршрут' if taxi else 'Адрес'}: {clean['address']}",
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
    from urllib.parse import quote
    import cabinet
    import portal
    if not portal._same_origin(request):                 # за прокси хостинга Host бывает внутренним: проверка та же, что в портале
        return cabinet._html("<p>Запрос отклонён. Откройте сайт заново и повторите.</p><p><a class='btn' href='/cabinet'>В кабинет</a></p>", status=403)
    uid = await webchat.session_uid(request)
    if uid is None:
        return cabinet._html("<p>Чтобы отменить заявку, войдите в кабинет: откройте сайт с того же телефона и браузера, "
                             "где отправляли заявку, или войдите заново.</p><p><a class='btn' href='/chat/login'>Войти</a></p>", status=401)
    try:
        rid = int(request.match_info["rid"])
    except ValueError:
        return web.Response(status=404)
    tg = await cabinet.canonical_tg(uid)           # те же владельцы, что и в списке кабинета: сессия и её Telegram
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"UPDATE web_requests SET status='rejected', admin_note=?, {db.touch()} "
            "WHERE id=? AND web_uid IN (?, ?) AND status IN ('new','work')", ("Отменена клиентом", rid, uid, tg))
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
        if not row or row["web_uid"] not in (uid, tg):
            raise web.HTTPFound(f"/cabinet?m={quote('Эта заявка не найдена в вашем кабинете.')}")
        msg = "Эту заявку уже нельзя отменить самостоятельно — напишите нам в чат."
    raise web.HTTPFound(f"/cabinet?m={quote(msg)}")
