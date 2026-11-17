"""Веб-заявка: страница /request для клиентов, у которых Telegram без VPN не открывается.

Страница без внешних ресурсов и без JavaScript (грузится на любой связи). Заявка сохраняется в web_requests,
админу сразу уходит сообщение в Telegram с телефоном: дальше он связывается с клиентом по телефону или СМС.
"""
import html as _html
import json
import logging
import secrets
import time
from datetime import date, datetime

from aiohttp import web

import db
import geo
import profanity
import timerules
import orders
import pricing
import webchat
import brand
import talk
import texts
from config import config
from utils import fmt_local, looks_fake_phone, normalize_phone, now_utc, rub

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


def _now() -> datetime:
    return datetime.now(config.tz)


def _today() -> date:
    return _now().date()


def _iso(v: str):
    try:
        return date.fromisoformat(v)
    except ValueError:
        return None


def _when(cat: str, form: dict, errors: dict, express: bool = False) -> str:
    """Дата и время из календаря → текст для админа: «16.11.2026, 10:00» или «с 16.11.2026 по 20.11.2026».
    Время проверяется по общим правилам (timerules): 06:00–22:00, 2 часа на подбор, «Экспресс», график админов."""
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
    if not (len(t) == 5 and t[2] == ":" and t.replace(":", "").isdigit()):
        errors["when_time"] = "Выберите время начала."
        return f"{d:%d.%m.%Y}"
    now = _now()
    dt = datetime(d.year, d.month, d.day, int(t[:2]), int(t[3:]), tzinfo=now.tzinfo)
    verdict = timerules.check(dt, now, cat, express)
    if not verdict.ok:
        errors["when_time"] = verdict.message
    return f"{d:%d.%m.%Y}, {t}" + (" · ⚡ Экспресс" if express and not verdict.express_blocked else "")


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
    if not phone or looks_fake_phone(phone):
        errors["phone"] = "Проверьте номер: он выглядит выдуманным. Нужен настоящий, например: 8 916 204-58-31."
    if not clean["name"]:
        errors["name"] = "Напишите, как к вам обращаться."
    if not clean["address"]:
        errors["address"] = "Напишите адрес: улица и дом."
    for k in ("name", "pet", "comment", "address", "dest", "when"):  # мат не принимаем ни в одном поле
        if clean.get(k) and k not in errors and profanity.has_profanity(clean[k]):
            errors[k] = profanity.MESSAGE
    if not clean["service_id"].isdigit():
        errors["service_id"] = "Выберите услугу."
    if not form.get("consent"):
        errors["consent"] = "Нужно согласие на обработку данных."
    cat = g("service")
    clean["service"] = cat if cat in SERVICE_TITLE else ""
    for k in ("when_date", "when_to", "when_time"):
        clean[k] = g(k)[:10]
    if clean["service"]:
        clean["express"] = bool(form.get("express")) and cat != "boarding" and timerules.express_available(_now())
        clean["when"] = _when(cat, form, errors, bool(form.get("express")) and cat != "boarding")
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
            f'<option value="{s["id"]}" data-cat="{cat}"{" selected" if str(s["id"]) == str(values.get("service_id")) else ""}>'
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
<div class="field field-wide">{addr_field("address", "Адрес", "например, Химки, улица Победы, 3", values, errors)}</div>
<div class="field"><label for="pet">Питомец</label><input id="pet" name="pet" maxlength="120" value="{v("pet")}" placeholder="например, Рекс, собака, лабрадор, 3 года">{err("pet")}</div>
<div class="field"><label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name" placeholder="например, Анна">{err("name")}</div>
<div class="field"><label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 916 204-58-31">{err("phone")}</div>
<div class="field field-wide"><label for="comment">Комментарий</label><textarea id="comment" name="comment" maxlength="500">{v("comment")}</textarea>{err("comment")}</div>
</div>
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Отправить заявку</button><p class="form-note">Нажимая кнопку, вы отправляете заявку на подбор услуги. Это не обязывает вас подтверждать заказ.</p></form>{PROF_JS}{LIVE_JS}{ADDR_JS}</main>{brand.public_footer()}</body></html>"""


HELP = '<button type="button" class="help" data-tip="{tip}" aria-label="Подсказка">?</button>'
TIPS = {
    "time": f"Услуга оказывается с {timerules.SERVICE_FROM:02d}:00 до {timerules.SERVICE_TO:02d}:00. Нам нужно минимум 2 часа, чтобы подобрать трёх исполнителей; "
            f"администраторы принимают заявки с {timerules.ADMIN_FROM:02d}:00 до {timerules.ADMIN_TO:02d}:00. Недоступное время затемнено. Нужно раньше? Поставьте галочку «Экспресс».",
}
BURGUNDY = "#7a1f3d"
SERVICE_CSS = """
.help{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;margin:0 0 0 6px;padding:0;border-radius:50%;border:1.5px solid var(--line,#bbb);background:#fff;color:inherit;font:700 .85rem/1 inherit;cursor:pointer;vertical-align:middle;min-height:0;width:26px}
.tip{margin:8px 0 0;padding:10px 12px;border-radius:12px;background:var(--peach,#fdf1ea);font-size:.95rem}
.slots{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin:8px 0 0}
.slot{margin:0;padding:11px 0;min-height:44px;border-radius:12px;border:1.5px solid var(--line,#ddd);background:#fff;color:inherit;font:600 1rem inherit;cursor:pointer}
.slot[disabled]{opacity:.35;cursor:not-allowed}.slot.on{background:var(--coral,#e8704a);border-color:var(--coral,#e8704a);color:#fff}
.slot-note{margin:8px 0 0;font-size:.95rem;color:var(--mute,#666)}.slot-note[hidden]{display:none}
.slot-note button{margin:8px 0 0;width:auto;padding:9px 14px;font-size:.95rem}
.express{margin-top:14px;padding:12px 14px;border:1.5px solid var(--line,#ddd);border-radius:12px;align-items:flex-start}
.express:has(input:checked){border-color:#7a1f3d;background:#f8eaee}.express input{accent-color:#7a1f3d}.express b{color:#7a1f3d}
.express.off{opacity:.5}
.variants{display:grid;gap:8px;margin:6px 0 0}.variant{display:flex;align-items:center;gap:10px;font-weight:400;margin:0;
padding:12px 14px;border:1.5px solid var(--line,#ddd);border-radius:12px;cursor:pointer}.variant input{width:auto;margin:0}
.variant b{margin-left:auto;white-space:nowrap}.variant input{accent-color:var(--coral)}.variant:has(input:checked){border-color:var(--coral);background:var(--peach)}
.step{font-size:1.05rem;margin:22px 0 2px}.row2{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}.row2>div{min-width:0}.row2 input{min-width:0;max-width:100%;box-sizing:border-box}.row2 label{margin-top:8px}
.other{font-size:.95rem}
.addr-in{position:relative}.addr-in input{padding-left:40px;background:#fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='20' height='20' viewBox='0 0 24 24' fill='none' stroke='%23999' stroke-width='2' stroke-linecap='round'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='M20 20l-3.5-3.5'/%3E%3C/svg%3E") 12px 50%/20px no-repeat}
.sug-box{position:absolute;left:0;right:0;top:calc(100% + 4px);z-index:30;display:none;background:#fff;border:1px solid var(--line,#ddd);border-radius:14px;box-shadow:0 10px 28px rgba(0,0,0,.16);overflow:hidden}
.sug-box.open{display:block}.sug-box:empty{display:none}
.sug{display:block;width:100%;text-align:left;font:inherit;padding:11px 14px;border:0;border-bottom:1px solid var(--line,#eee);border-radius:0;background:#fff;color:inherit;cursor:pointer;margin:0}
.sug:last-child{border-bottom:0}.sug-main{display:block;font-weight:600}.sug-sub{display:block;font-size:.85rem;color:var(--mute,#777);margin-top:1px}
.sug:hover,.sug:focus,.sug.on{background:var(--peach,#fdf1ea)}.sug-note{font-size:.92rem;color:var(--mute,#777);padding:12px 14px}
.addr-tools{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 0}
.addr-btn{width:auto;margin:0;padding:9px 14px;font:inherit;font-size:.92rem;border:1.5px solid var(--line,#ddd);border-radius:999px;background:#fff;color:inherit;cursor:pointer}
.addr-btn:hover{border-color:var(--coral);background:var(--peach,#fdf1ea)}.addr-btn[disabled]{opacity:.55;cursor:progress}
.addr-note{font-size:.92rem;color:var(--mute,#777);margin-top:6px}.addr-note:empty{display:none}.addr-note.bad{color:var(--err,#c0392b)}
.zc-modal{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:3000;display:flex;align-items:center;justify-content:center}
.zc-modal-box{width:min(560px,100%);height:min(760px,100dvh);background:#fff;border-radius:16px;overflow:hidden;display:flex;flex-direction:column}
.zc-modal-bar{display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--line,#ddd)}
.zc-modal-x{width:auto;margin:0;padding:8px 14px;font:inherit;border:1.5px solid var(--line,#ddd);border-radius:999px;background:#fff;cursor:pointer}
.zc-modal-box iframe{flex:1;width:100%;border:0}
@media(max-width:600px){.zc-modal-box{width:100%;height:100dvh;border-radius:0}}
"""

PROF_JS = r"""<script>(function(){var PF=__PROF__,PC=PF.patterns.map(function(p){return new RegExp(p)}),PL=PF.latin.map(function(p){return new RegExp(p)}),
LOOK={a:'а',c:'с',e:'е',o:'о',m:'м',p:'р',h:'н',x:'х',y:'у',t:'т',k:'к',b:'в'};
function col(w){return w.replace(/(.)\1+/g,'$1')}
function any(L,c){for(var i=0;i<L.length;i++)if(L[i].test(c))return true;return false}
function hit(tok){var vs=tok.indexOf('*')<0?[tok]:'аеиоуыэюя'.split('').map(function(v){return tok.replace(/\*/g,v)});
for(var i=0;i<vs.length;i++){var t=vs[i],hl=/[a-z]/.test(t),hc=/[а-я]/.test(t);
if(hl&&!hc){if(any(PL,col(t)))return true;continue}
if(hl)t=t.replace(/[a-z]/g,function(ch){return LOOK[ch]||ch});
if(any(PC,col(t)))return true}return false}
var AL=new RegExp(PF.allow,'g');window.zcProf=function(text){var toks=String(text||'').toLowerCase().replace(/ё/g,'е').replace(AL,' ').match(/[a-zа-я*]+/g)||[],run=[];
for(var i=0;i<toks.length;i++)if(hit(toks[i]))return true;
toks.push('');for(var j=0;j<toks.length;j++){var t=toks[j];if(t.length===1&&t!=='*'){run.push(t);continue}
if(run.length>=3&&hit(run.join('')))return true;run=[]}return false};
})();</script>""".replace("__PROF__", json.dumps(profanity.js_spec(), ensure_ascii=False))


LIVE_JS = """<script>(function(){
var f=document.querySelector('main form');if(!f)return;f.noValidate=true;
var M={phone:'Проверьте номер: он выглядит выдуманным. Нужен настоящий, например: 8 916 204-58-31.',name:'Напишите, как к вам обращаться.',
address:'Напишите адрес: улица и дом.',house:'Укажите номер дома: без него водитель не найдёт место. Например: Ленина улица, 5.',house2:'Укажите номер дома: без него мы не найдём место. Например: Ленина улица, 5.',
dest:'Напишите, куда отвезти питомца.',same:'Адреса «Откуда» и «Куда» совпадают. Напишите, куда отвезти питомца.',
past:'Дата уже прошла — выберите сегодня или позже.',time:'Выберите время начала.',to:'Дата «по» раньше даты «с».',
consent:'Нужно согласие на обработку данных.',prof:'Уберите нецензурные и оскорбительные слова: так мы не сможем принять заявку.',service_id:'Выберите услугу.'};
function val(n){var e=f.elements[n];return e?String(e.value||'').trim():''}
function phoneOk(t){var d=t.replace(/\\D/g,''),n;if(d.length==11&&'78'.indexOf(d[0])>=0)n=d.slice(1);else if(d.length==10)n=d;else return false;
 if('3489'.indexOf(n[0])<0||/(\\d)\\1{5}/.test(n))return false;var s={},k=0,i,a=1,b=1;for(i=0;i<10;i++){if(!s[n[i]]){s[n[i]]=1;k++}}if(k<=3)return false;
 for(i=1;i<10;i++){a=(+n[i]-+n[i-1]==1)?a+1:1;b=(+n[i-1]-+n[i]==1)?b+1:1;if(a>=6||b>=6)return false}return true}
function cat(){var c=val('service');if(c)return c;var s=f.elements.service_id;
 if(s&&s.tagName=='SELECT'&&s.selectedOptions&&s.selectedOptions[0])return s.selectedOptions[0].getAttribute('data-cat')||'';
 return ''}
function taxi(){return cat()=='taxi'}
function needHouse(){return cat()!='boarding'}
function pf(n){return window.zcProf&&window.zcProf(val(n))?M.prof:''}
var R={
phone:function(){return phoneOk(val('phone'))?'':M.phone},
name:function(){return val('name')?pf('name'):M.name},
pet:function(){return pf('pet')},
comment:function(){return pf('comment')},
address:function(){var a=val('address');if(!a)return M.address;if(pf('address'))return M.prof;if(needHouse()&&!/\\d/.test(a.replace(/(,|\\s)*(кв|квартира|офис|оф)\\.?\\s*\\d+.*$/i,'')))return taxi()?M.house:M.house2;return ''},
dest:function(){if(!taxi())return '';var d=val('dest');if(!d)return M.dest;if(pf('dest'))return M.prof;if(d.toLowerCase()==val('address').toLowerCase())return M.same;
 if(!/\\d/.test(d))return M.house;return ''},
when_date:function(){var e=f.elements.when_date;if(!e||!e.value)return '';return e.min&&e.value<e.min?M.past:''},
when_time:function(){var e=f.elements.when_time;if(!e||!f.querySelector('.timebox'))return '';if(!e.value)return M.time;return window.zcTime?window.zcTime.bad():''},
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

TIME_JS = r"""<script>(function(){
var f=document.querySelector('main form'),box=document.getElementById('slots');if(!f||!box)return;
var tb=f.querySelector('.timebox'),cat=tb.getAttribute('data-cat'),t=f.elements.when_time,d=f.elements.when_date,x=f.elements.express,
note=document.getElementById('slot-note'),row=document.getElementById('express-row'),tip=document.getElementById('tip-box'),
TIPS=__TIPS__,last=null,seq=0,idle=null;
f.classList.add('js-slots');t.style.display='none';
function say(html,fix){note.hidden=!html;note.textContent=html||'';
 if(fix){var b=document.createElement('button');b.type='button';b.className='btn';b.textContent='Подставить ближайшее время: '+fix.label;
  b.addEventListener('click',function(){d.value=fix.date;t.value=fix.time;touch();load()});note.appendChild(document.createElement('br'));note.appendChild(b)}}
function touch(){t.dispatchEvent(new Event('change',{bubbles:true}))}
function draw(r){last=r;box.innerHTML='';
 (r.slots||[]).forEach(function(s){var b=document.createElement('button');b.type='button';b.className='slot'+(t.value===s.t?' on':'');b.textContent=s.t;
  if(!s.ok){b.disabled=true;b.setAttribute('aria-disabled','true')}
  else b.addEventListener('click',function(){t.value=s.t;touch();draw(last);hint()});box.appendChild(b)});
 if(row){row.classList.toggle('off',!r.express_available);if(!r.express_available&&x)x.disabled=true;else if(x)x.disabled=false}
 hint()}
function hint(){var bad=badNow();
 if(bad&&last){var fix=last.earliest_time?{date:last.earliest_date,time:last.earliest_time,label:last.earliest}:null;say(bad,fix);arm()}
 else if(last&&!t.value&&last.note){say(last.note,null);arm()}
 else{say('',null);clearTimeout(idle)}}
function badNow(){if(!last||!t.value)return '';var s=(last.slots||[]).filter(function(q){return q.t===t.value})[0];
 if(s)return s.ok?'':(last.note||'Это время недоступно. Выберите другое.');
 return ''}
function arm(){clearTimeout(idle);idle=setTimeout(function(){if(!t.value||badNow())say((note.textContent||'')+' Почти готово: выберите любое доступное время, остальное займёт минуту.',null)},12000)}
function load(){var my=++seq;if(!d.value){box.innerHTML='';say('Сначала выберите дату.',null);return}
 fetch('/api/slots?cat='+encodeURIComponent(cat)+'&date='+encodeURIComponent(d.value)+'&express='+(x&&x.checked?'1':'0'),{cache:'no-store'})
 .then(function(r){return r.json()}).then(function(r){if(my!==seq)return;draw(r)}).catch(function(){t.style.display='';box.innerHTML=''})}
d.addEventListener('change',load);d.addEventListener('input',load);if(x)x.addEventListener('change',load);
t.addEventListener('input',function(){if(last)draw(last)});
Array.prototype.forEach.call(f.querySelectorAll('.help'),function(b){b.addEventListener('click',function(){var k=b.getAttribute('data-tip');
 if(!tip.hidden&&tip.getAttribute('data-k')===k){tip.hidden=true;return}tip.textContent=TIPS[k]||'';tip.setAttribute('data-k',k);tip.hidden=false;
 b.closest('label').insertAdjacentElement('afterend',tip)})});
window.zcTime={bad:function(){return badNow()}};
if(!d.value){var n=new Date(),p=function(v){return String(v).padStart(2,'0')};d.value=n.getFullYear()+'-'+p(n.getMonth()+1)+'-'+p(n.getDate())}
load();
})();</script>""".replace("__TIPS__", json.dumps(TIPS, ensure_ascii=False))


ADDR_JS = r"""<script src="/addr.js"></script><script>(function(){
var Z=window.zcAddr;if(!Z)return;
Array.prototype.forEach.call(document.querySelectorAll('[data-addr]'),function(w){
var id=w.getAttribute('data-addr'),inp=document.getElementById(id),ok=w.querySelector('input[name='+id+'_ok]'),box=document.getElementById(id+'-sug'),
note=w.querySelector('.addr-note'),house=w.getAttribute('data-house')==='1',pt=w.getAttribute('data-pt')||'',coords=null,b1=w.querySelector('[data-act=locate]'),b2=w.querySelector('[data-act=map]');
if(!inp||!ok||!box)return;
function say(t,bad){note.textContent=t||'';note.className='addr-note'+(bad?' bad':'')}
function fire(){inp.dispatchEvent(new Event('change',{bubbles:true}))}
function done(text,sure){inp.value=text;ok.value=sure?text:'';box.innerHTML='';box.classList.remove('open');fire()}
function street(text){inp.value=text;ok.value='';fire();say('Нашли улицу. Допишите номер дома или уточните на карте.',true);inp.focus()}
function got(p,flat){coords={lat:p.lat,lon:p.lon};var t=p.label||'';
 if(!t){say('Адрес по точке не определился. Впишите его в строку.',true);inp.focus();return}
 if(flat)t+=', кв. '+flat;
 if(house&&!/\d/.test(String(p.label).replace(/(,|\s)*(кв|квартира|офис|оф)\.?\s*\d+.*$/i,''))){street(t);return}
 done(t,true);say('Проверьте адрес. Если точка неточная, нажмите «Уточнить на карте».')}
Z.bind(inp,{box:box,min:4,onChange:function(){ok.value='';coords=null;say('')},onPick:function(it){coords=it.lat!=null?{lat:it.lat,lon:it.lon}:null;done(it.label,true);say('')}});
box.addEventListener('click',function(e){var b=e.target.closest&&e.target.closest('.sug');
 if(!b||b.querySelector('.sug-main'))return;done(b.getAttribute('data-pick'),true);say('')});
if(box.children.length)box.classList.add('open');
b1.addEventListener('click',function(){b1.disabled=true;say('Определяю, где вы…');
 Z.locate().then(function(c){
  if(!Z.inBox(c.lat,c.lon)){say('Похоже, вы вне Москвы и области. Впишите адрес или отметьте точку на карте.',true);return}
  return Z.reverse(c.lat,c.lon).then(function(r){got({lat:c.lat,lon:c.lon,label:r.label});},function(){
   coords={lat:c.lat,lon:c.lon};say('Место определили, а адрес по нему не нашёлся. Впишите адрес или отметьте точку на карте.',true)})
 },function(e){say(Z.locateError(e),true)}).then(function(){b1.disabled=false},function(){b1.disabled=false})});
b2.addEventListener('click',function(){Z.openMap({pt:pt,lat:coords&&coords.lat,lon:coords&&coords.lon,label:inp.value.trim(),house:house?1:0,
 onPick:function(p){got(p,p.flat)}})});
});
})();</script>"""


def addr_field(key: str, label: str, placeholder: str, values: dict, errors: dict, *, house: bool = True, pt: str = "") -> str:
    """Строка адреса, одна на все услуги: подсказки с карты по мере набора, «Определить по геопозиции» и «Уточнить на карте»
    (геопозиция в России бывает неточной, поэтому точку всегда можно поправить), ошибка прямо под строкой."""
    val = lambda k: _e(values.get(k, ""))  # noqa: E731
    opts = "".join(f'<button type="button" class="sug" data-for="{key}" data-pick="{_e(o)}">{_e(o)}</button>'
                   for o in (values.get(key + "_opts") or []))
    err = f'<div class="err">{_e(errors[key])}</div>' if key in errors else ""
    return (f'<label for="{key}">{label}</label><div class="addr" data-addr="{key}" data-house="{1 if house else 0}" data-pt="{pt}">'
            f'<div class="addr-in"><input id="{key}" name="{key}" maxlength="200" value="{val(key)}" placeholder="{_e(placeholder)}" '
            f'autocomplete="off" autocapitalize="sentences" enterkeyhint="search" role="combobox" aria-autocomplete="list">'
            f'<div class="sug-box" id="{key}-sug">{opts}</div></div>{err}'
            f'<input type="hidden" name="{key}_ok" value="{val(key + "_ok")}">'
            '<div class="addr-tools"><button type="button" class="addr-btn" data-act="locate">📍 Определить по геопозиции</button>'
            '<button type="button" class="addr-btn" data-act="map">🗺 Уточнить на карте</button></div>'
            '<div class="addr-note" role="status" aria-live="polite"></div></div>')


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
        when = (f'<label for="when_date">Дата</label><input id="when_date" name="when_date" type="date" min="{today}" value="{v("when_date")}">{err("when_date")}'
                f'<div class="timebox" data-cat="{cat}"><label for="when_time">Время начала {HELP.format(tip="time")}</label>'
                f'<input id="when_time" name="when_time" type="time" step="900" value="{v("when_time")}">{err("when_time")}'
                '<div id="slots" class="slots" role="group" aria-label="Время начала"></div><div id="slot-note" class="slot-note" role="status" aria-live="polite"></div></div>'
                f'<label class="check express" id="express-row"><input type="checkbox" name="express" value="on"{" checked" if values.get("express") else ""}>'
                '<span><b>⚡ Экспресс</b> — заявка за считанные минуты до начала, без ожидания 2 часов. Услуга стоит дороже. '
                f'Работает с {timerules.ADMIN_FROM:02d}:00 до {timerules.ADMIN_TO:02d}:00, пока на связи администраторы.</span></label>'
                '<div class="tip" id="tip-box" role="note" hidden></div>')
    if cat == "taxi":
        where = (addr_field("address", "Откуда", "например, Москва, Тверская улица, 12", values, errors, pt="a")
                 + addr_field("dest", "Куда", "например, Москва, Ленинский проспект, 10", values, errors, pt="b")
                 + '<p class="hint">Начните писать адрес и выберите нужный из списка: так машина приедет точно.</p>')
    elif cat == "boarding":
        where = (addr_field("address", "Ваш район или адрес", "например, Химки, Ленинский проспект", values, errors, house=False)
                 + '<p class="hint">Подберём передержку поближе. Дом можно не указывать.</p>')
    else:
        where = (addr_field("address", "Адрес", "например, Химки, улица Победы, 3", values, errors)
                 + '<p class="hint">Начните писать адрес и выберите нужный из списка.</p>')
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
<label for="pet">Питомец</label><input id="pet" name="pet" maxlength="120" value="{v("pet")}" placeholder="например, Рекс, собака, лабрадор, 3 года">{err("pet")}
<div class="row2"><div><label for="name">Ваше имя</label><input id="name" name="name" maxlength="80" value="{v("name")}" autocomplete="name" placeholder="например, Анна">{err("name")}</div>
<div><label for="phone">Телефон</label><input id="phone" name="phone" type="tel" maxlength="30" value="{v("phone")}" autocomplete="tel" placeholder="8 916 204-58-31">{err("phone")}</div></div>
<label for="comment">Комментарий</label><textarea id="comment" name="comment" maxlength="500" placeholder="характер, привычки, что важно знать">{v("comment")}</textarea>{err("comment")}
<div class="hp"><label>Не заполняйте <input name="site" tabindex="-1" autocomplete="off"></label></div>
<label class="check"><input type="checkbox" name="consent"{checked}><span>Согласен(на) на обработку персональных данных, <a href="/policy">политика</a></span></label>{err("consent")}
<button class="btn" type="submit">Отправить заявку</button><p class="form-note">Это не заказ: администратор сначала подтвердит цену и исполнителя.</p></form>{PROF_JS}{TIME_JS}{LIVE_JS}{ADDR_JS}</main>{brand.public_footer()}</body></html>"""


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


def render_done(request_id: int, express: bool = False) -> str:
    x = '<p style="padding:10px 12px;border-radius:12px;background:#f8eaee;color:#7a1f3d;font-weight:700">⚡ Экспресс: ваша заявка в приоритете, администратор свяжется с вами в первую очередь.</p>' if express else ""
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Зооконсьерж — заявка принята</title>
{brand.HEAD}<style>{CSS}</style></head><body>{brand.public_header()}<main class="public-main public-form"><h1>Заявка принята</h1>{x}
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


async def check_addresses(clean: dict, errors: dict, cat: str) -> None:
    """Адрес в любой услуге: найден на карте, с номером дома. Выбран из подсказок или набран ровно как в подсказке — принимаем;
    без дома, не найден или не выбран — просим исправить, показывая варианты. Карта недоступна — клиента не блокируем,
    но номер дома требуем всё равно: улица без дома — не адрес. Передержка: подойдёт и район без дома, но место должно существовать."""
    taxi = cat == "taxi"
    for key in ("address", "dest"):
        text = clean.get(key) or ""
        if key in errors or not text:
            continue
        if cat != "boarding" and not geo.has_house_number(text):
            errors[key] = ("Укажите номер дома: без него водитель не найдёт место. Например: Ленина улица, 5." if taxi
                           else "Укажите номер дома: без него мы не найдём место. Например: Ленина улица, 5.")
            continue
        if clean.get(key + "_ok") == text:
            continue
        places = await geo.search(text)
        if places is None:
            continue
        if not places:
            errors[key] = ("Не нашёл такой район или адрес. Напишите район или улицу, например: Химки, Ленинский проспект."
                           if cat == "boarding" else
                           "Не нашёл такой адрес на карте. Проверьте написание, добавьте город и номер дома.")
            continue
        if cat == "boarding":
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


async def api_slots(request: web.Request) -> web.Response:
    """Сетка времени выбранного дня для страницы заявки: какие слоты доступны и почему остальные нет.
    Единый источник правды — timerules; браузер только рисует ответ. Параметры: cat, date (ГГГГ-ММ-ДД), express (1/0)."""
    q = request.query
    cat, d, express = q.get("cat", ""), _iso(q.get("date", "")), q.get("express") in ("1", "true", "on")
    now = _now()
    out = {"slots": [], "earliest": "", "express_available": timerules.express_available(now), "note": "",
           "service_from": timerules.SERVICE_FROM, "service_to": timerules.SERVICE_TO}
    if d and cat in SERVICE_TITLE and cat != "boarding":
        eff = express and out["express_available"]
        e = timerules.earliest(now, eff)
        out["earliest"] = e.strftime("%H:%M") if e.date() == d else timerules.fmt_earliest(e, now)
        out["earliest_date"], out["earliest_time"] = e.date().isoformat(), e.strftime("%H:%M")
        past = ""
        for m in range(timerules.SERVICE_FROM * 60, timerules.SERVICE_TO * 60 + 1, timerules.STEP * 2):
            dt = datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=now.tzinfo)
            v = timerules.check(dt, now, cat, express)
            out["slots"].append({"t": f"{m // 60:02d}:{m % 60:02d}", "ok": v.ok})
            if not v.ok and not out["note"] and v.code in ("too_soon", "admin_closed"):
                out["note"] = v.message
            elif not v.ok and v.code == "past" and not past:
                past = v.message
        out["note"] = out["note"] or past
        if express and not out["express_available"]:
            out["note"] = out["note"] or ("Экспресс доступен только с "
                                          f"{timerules.ADMIN_FROM:02d}:00 до {timerules.ADMIN_TO:02d}:00, пока работают администраторы.")
    return web.json_response(out, headers={"Cache-Control": "no-store"})


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
    chosen = next((x for x in services if str(x["id"]) == clean["service_id"]), None)
    if chosen and "bot" not in errors:
        await check_addresses(clean, errors, chosen["category"])
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
            """INSERT INTO web_requests (service_id, name, phone, address, when_text, pet, comment, web_uid, ip, device, express)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (svc["id"], clean["name"], clean["phone"], clean["address"], clean["when"],
             clean["pet"], clean["comment"], web_uid, ip, device, 1 if clean.get("express") else 0))
        rid = cur.lastrowid
    if clean["nonce"]:
        SEEN[clean["nonce"]] = (now, rid)
    text = "\n".join(filter(None, [
        f"🌐 Заявка с сайта №{rid}" + (" · ⚡ ЭКСПРЕСС: срочно, свяжитесь с клиентом сразу" if clean.get("express") else ""),
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
    resp = web.Response(text=render_done(rid, bool(clean.get("express"))), content_type="text/html", headers={"Cache-Control": "no-cache"})
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
