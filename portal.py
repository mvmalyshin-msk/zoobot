"""Портал администратора /portal: одно место для всех заявок (Telegram, чат на сайте, форма на сайте),
карточка заказа со всеми действиями, переписка с клиентами и исполнителями, общий чат клиента и исполнителя.

Вход: одноразовая ссылка из бота (/webadmin или кнопка «Войти через Telegram»), код в Telegram или код на почту
(работает без VPN). Права те же, что у админа в боте: только ADMIN_IDS. Все действия идут через те же функции,
что и кнопки в Telegram (orders.change_status и т. д.), поэтому клиент получает те же уведомления.
"""
import contextvars
import hashlib
import html as _html
import json
import logging
import re
import secrets
import time
from urllib.parse import quote, urlencode

from aiohttp import web

import brand
import ribbons
import db
import mailer
import passkeys
import orders
import payinfo
import talk
import texts
import webchat
from config import config
from utils import from_db, fmt_local, now_utc, rub, to_local

log = logging.getLogger(__name__)
CODE_LIMIT = 5            # запросов кода с одного адреса за 10 минут
FAIL_LIMIT = 20           # неверных кодов с одного адреса за час
CODE_TRIES = 5            # попыток на один код
_hits: dict[str, list[float]] = {}
_pk_hits: dict[str, list[float]] = {}
_fails: dict[str, list[float]] = {}

_NEW_N: contextvars.ContextVar[int] = contextvars.ContextVar("portal_new_n", default=0)  # новых заявок — для значка в переключателе
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]
WEEKDAYS_RU = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
SOURCES = {"tg": "Telegram", "web": "Чат на сайте", "form": "Форма на сайте"}
REQ_STATUSES = {"new": "Новая", "work": "В работе", "done": "Готово", "rejected": "Отменена"}
CHIP = {"walk": "walk", "sitter": "nanny", "boarding": "boarding", "taxi": "taxi"}
STATUS_COLOR = {"created": "var(--coral)", "confirmed": "var(--taxi)", "paid": "var(--nanny)",
                "assigned": "var(--boarding)", "in_progress": "var(--boarding)", "completed": "#8a8178",
                "cancelled": "#b9b0a6"}


# Цвет заявки: новая — белая, ждёт оплаты — светло-жёлтая, в работе (подтверждена с обеих сторон) — зелёная,
# выполнена — тёмно-серая с ярко-зелёной галочкой, отменена — приглушённая
STATUS_CLASS = {"created": "st-new", "confirmed": "st-wait", "paid": "st-work", "assigned": "st-work",
                "in_progress": "st-work", "completed": "st-done", "cancelled": "st-cancelled"}
REQ_CLASS = {"new": "st-new", "work": "st-work", "done": "st-done", "rejected": "st-cancelled"}
TICK = '<i class="tick">✓</i>'


def cancel_label(actor: str | None) -> str:
    """Кто отменил: «Отменил клиент» / «Отменил администратор» / просто «Отменён»."""
    a = actor or ""
    return "Отменил клиент" if a.startswith("client") else "Отменил администратор" if a.startswith(("admin", "portal")) else "Отменён"


def _e(v) -> str:
    return _html.escape("" if v is None else str(v), quote=True)


def _ip(request: web.Request) -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote or "?")[:64]


def _limit(store: dict, key: str, n: int, window: int) -> bool:
    """True — лимит исчерпан."""
    now = time.time()
    hits = [t for t in store.get(key, []) if now - t < window]
    over = len(hits) >= n
    if not over:
        hits.append(now)
    store[key] = hits
    return over


async def _admin(request: web.Request) -> int | None:
    uid = await webchat.session_uid(request, admin=True)
    return uid if uid is not None and uid in config.admin_ids else None


def _same_origin(request: web.Request) -> bool:
    origin = request.headers.get("Origin")
    if not origin or origin == "null":
        return True  # браузеры шлют Origin на POST; без него — не из чужой страницы (cookie SameSite=Lax)
    from urllib.parse import urlparse
    allowed = {request.host, request.headers.get("X-Forwarded-Host"), urlparse(config.webapp_url).netloc}
    return urlparse(origin).netloc in {h for h in allowed if h}


async def _new_count() -> int:
    """Сколько заявок ждут первого ответа: новые заказы и новые заявки с сайта."""
    a = await db.fetch_one("SELECT COUNT(*) AS n FROM orders WHERE status='created'")
    b = await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests WHERE status='new'")
    return int(a["n"]) + int(b["n"])


def admin_page(handler):
    async def wrapper(request: web.Request):
        uid = await _admin(request)
        if request.method == "POST" and (uid is None or not _same_origin(request)):
            return web.Response(status=403, text="Нет доступа")
        if uid is None:
            if request.path.startswith("/portal/api/"):
                return web.json_response({"error": "login"}, status=401)
            return _html_resp(login_page())
        _NEW_N.set(await _new_count())
        resp = await handler(request, uid)
        import analytics                              # поздний импорт: analytics сам импортирует portal
        await analytics.mark_owner(request, resp)     # свои заходы владельца в аналитику не идут
        return resp
    return wrapper


def _html_resp(body: str, status: int = 200) -> web.Response:
    return web.Response(text=body, status=status, content_type="text/html",
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                 "X-Frame-Options": "DENY"})


def _redirect(path: str, msg: str | None = None) -> web.Response:
    if msg:
        path += ("&" if "?" in path else "?") + urlencode({"m": msg})
    return web.Response(status=303, headers={"Location": path})


CSS = """
body{min-height:100vh}
.top{position:sticky;top:0;z-index:5;background:var(--cream);border-bottom:1.5px solid var(--line);padding:10px 14px;
  padding-top:max(10px,env(safe-area-inset-top));display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;justify-content:space-between}
.top .brand{font-size:20px}
.top details.menu{position:relative}.top details.menu summary{cursor:pointer;list-style:none;padding:6px 14px;border-radius:999px;background:var(--card);border:1.5px solid var(--line);font-weight:700}
.top details.menu summary::-webkit-details-marker{display:none}.top details.menu[open] summary{background:var(--ink);color:#fff}
.top details.menu nav{position:absolute;right:0;top:calc(100% + 6px);z-index:9;flex-direction:column;background:var(--cream);border:1.5px solid var(--line);border-radius:16px;padding:8px;min-width:200px;box-shadow:0 8px 24px rgba(0,0,0,.15)}
.top nav{display:flex;gap:6px;flex-wrap:wrap}
.top nav a{text-decoration:none;color:var(--ink);padding:6px 12px;border-radius:999px;font-weight:700;font-size:15px;background:var(--card)}
.top nav .asclient{margin:0}.top nav .asclient button{font:inherit;font-weight:700;font-size:15px;border:0;cursor:pointer;color:var(--ink);padding:6px 12px;border-radius:999px;background:var(--card);width:100%;text-align:left}
.top nav a.on{background:var(--coral);color:#fff}
main{max-width:900px;margin:0 auto;padding:14px 12px 60px}
h1{font-size:1.45rem;margin:.3em 0 .5em}h2{font-size:1.15rem;margin:1.2em 0 .5em}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
details.filter{margin:0 0 12px}details.filter summary{display:inline-block;cursor:pointer;list-style:none;padding:8px 16px;border-radius:999px;background:var(--card);border:1.5px solid var(--line);font-weight:700}
details.filter summary::-webkit-details-marker{display:none}details.filter[open] summary{background:var(--ink);color:#fff}details.filter[open] summary small{color:#ddd}
.tabs a{padding:6px 12px;border-radius:999px;background:var(--peach);color:var(--ink);text-decoration:none;font-weight:700;font-size:14px}
.tabs a.on{background:var(--ink);color:#fff}
.list{display:flex;flex-direction:column;gap:8px}
.item{display:block;padding:12px 14px;text-decoration:none;color:var(--ink)}
.item .row{display:flex;justify-content:space-between;gap:8px;align-items:center;flex-wrap:wrap}
.item .who{font-weight:700}.item .sub{color:var(--mute);font-size:14px;margin-top:3px}
.badge{display:inline-block;border-radius:999px;padding:.1em .6em;font-size:12px;font-weight:700;color:#fff}
.src{display:inline-block;border-radius:999px;padding:.1em .6em;font-size:12px;font-weight:700;background:var(--peach);color:var(--ink)}
.pre{white-space:pre-wrap;padding:14px;line-height:1.45}
.actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
.actions form{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.btn.small{padding:.5em 1em;font-size:.92rem}.btn.danger{background:#fff;color:var(--err);border:1.5px solid var(--err)}
.flash{background:var(--peach);border-radius:14px;padding:10px 14px;margin-bottom:12px}
.muted{color:var(--mute)}
.msgs{display:flex;flex-direction:column;gap:6px;padding:10px;min-height:200px;max-height:60vh;overflow-y:auto}
.msg{max-width:85%;padding:8px 12px;border-radius:16px;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.in{background:var(--card);align-self:flex-start;box-shadow:0 1px 2px rgba(80,50,20,.1)}
.msg.out{background:var(--peach);align-self:flex-end}
.msg.room{background:#e3f4f2;align-self:flex-start}
.msg .t{display:block;font-size:11px;color:var(--mute);margin-top:3px}
.send{display:flex;gap:8px;margin-top:8px}.send input{flex:1;min-width:0}
#newbar{display:none;position:fixed;left:12px;right:12px;bottom:max(12px,env(safe-area-inset-bottom));z-index:9;text-align:center}
.top nav.seg{flex:1 0 100%;display:flex;gap:6px;flex-wrap:nowrap;margin-top:2px}
.top nav.seg a{flex:1 1 0;text-align:center;padding:9px 6px;font-size:15px;background:var(--peach);white-space:nowrap}
.top nav.seg a.on{background:var(--ink);color:#fff}
.seg .cnt{display:inline-block;min-width:1.5em;margin-left:6px;padding:0 .4em;border-radius:999px;background:var(--coral);color:#fff;font-size:13px;line-height:1.5}
.item.ord{display:flex;gap:12px;align-items:stretch}.item.ord .main{flex:1;min-width:0}
.stamp{flex:0 0 66px;display:flex;flex-direction:column;justify-content:center;align-items:center;border-radius:14px;padding:6px 4px;background:var(--peach);color:var(--ink);line-height:1.15}
.stamp b{font-size:21px;font-variant-numeric:tabular-nums}.stamp span{font-size:13px;font-weight:700;color:var(--mute)}
.stamp.new{background:var(--coral);color:#fff}.stamp.new span{color:#fff}
h3.day{margin:16px 4px 2px;font-size:13px;letter-spacing:.05em;text-transform:uppercase;color:var(--mute)}
.reg{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 12px;margin:0 0 12px;padding:10px 14px;border-radius:16px;background:var(--peach)}
.reg b{font-size:28px;font-variant-numeric:tabular-nums}.reg span{font-size:18px;font-weight:700}.reg em{font-style:normal;color:var(--mute);font-size:14px;flex-basis:100%}
.pager{display:flex;gap:6px;margin:0 0 12px}
.pager a,.pager .off{flex:1;text-align:center;padding:9px 6px;border-radius:999px;background:var(--card);border:1.5px solid var(--line);color:var(--ink);text-decoration:none;font-weight:700;font-size:15px}
.pager .off{opacity:.4}
.item.ord.st-new{background:#fff}
.item.ord.st-wait{background:#fff6dc;box-shadow:inset 5px 0 0 #e8b931}
.item.ord.st-work{background:#e1f5e6;box-shadow:inset 5px 0 0 #2e9e5b}
.item.ord.st-done{background:#45454a;color:#f1f1f3;box-shadow:inset 5px 0 0 #22e06b}
.item.ord.st-done .sub,.item.ord.st-done .stamp span{color:#c9c9d0}.item.ord.st-done .src{background:#5d5d64;color:#eee}
.item.ord.st-done .stamp{background:#35353a;color:#fff}
.item.ord.st-cancelled{background:#efe9e4;color:#7a7168;box-shadow:inset 5px 0 0 #c9473a}.item.ord.st-cancelled .who{text-decoration:line-through}
.stt{display:inline-flex;align-items:center;gap:6px}
.tick{font-style:normal;font-weight:900;font-size:22px;line-height:1;color:#22e06b}
.stbar{display:flex;align-items:center;gap:10px;border-radius:16px;padding:12px 16px;margin:0 0 12px;font-weight:800;font-size:1.1rem}
.stbar.st-new{background:#fff;border:1.5px solid var(--line)}.stbar.st-wait{background:#fff6dc}.stbar.st-work{background:#e1f5e6;color:#17683a}
.stbar.st-done{background:#45454a;color:#f1f1f3}.stbar.st-cancelled{background:#f6dcd8;color:#a3342a}
.others{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px}.others a{text-decoration:none;color:var(--ink)}
label{font-weight:700;display:block;margin:10px 0 4px}
input,select,textarea{max-width:100%}
"""

NOTIFY_JS = """<script>(function(){
var last=null,base=document.title;
function key(){try{return JSON.parse(localStorage.getItem('zc_portal')||'null')}catch(e){return null}}
function save(j){try{localStorage.setItem('zc_portal',JSON.stringify(j))}catch(e){}}
function tick(){fetch('/portal/api/summary',{credentials:'same-origin'}).then(function(r){return r.ok?r.json():null}).then(function(j){
 if(!j)return;var prev=key();
 if(prev&&(j.last_order>prev.last_order||j.last_request>prev.last_request||j.last_in>prev.last_in)){
   var what=j.last_in>prev.last_in?'Новое сообщение':'Новая заявка';
   document.title='🔔 '+base;var b=document.getElementById('newbar');if(b){b.style.display='block';b.firstChild.textContent=what+' — обновить'}
   try{if(window.Notification&&Notification.permission==='granted')new Notification('Зооконсьерж',{body:what})}catch(e){}
 }
 if(prev&&j.last_status>(prev.last_status||0)){
   // статус заказа сменился (клиент отменил, исполнитель начал и т. д.): сообщаем сразу и сами обновляем список или страницу этого заказа
   document.title='🔔 '+base;var b2=document.getElementById('newbar');if(b2){b2.style.display='block';b2.firstChild.textContent=j.status_text+' — обновить'}
   try{if(window.Notification&&Notification.permission==='granted')new Notification('Зооконсьерж',{body:j.status_text})}catch(e){}
   save(j);
   var p=location.pathname;
   if((/^\\/portal\\/?$/.test(p)||p==='/portal/order/'+j.status_order)&&!document.querySelector('input:focus,textarea:focus,select:focus'))location.reload();
   return;
 }
 save(j);
}).catch(function(){})}
tick();setInterval(tick,5000);
try{if(navigator.clearAppBadge)navigator.clearAppBadge()}catch(e){}
// Уведомления, которые приходят при закрытом приложении (Web Push). Подписка хранится в браузере/приложении,
// поэтому её нужно создать там, где человек пользуется порталом: на iPhone — в приложении с экрана «Домой».
var H={'X-Zc':'1','Content-Type':'application/json'};
var canPush='serviceWorker' in navigator&&'PushManager' in window&&!!window.Notification;
function keyBytes(k){k=k.replace(/-/g,'+').replace(/_/g,'/');while(k.length%4)k+='=';var b=atob(k),a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a}
function pushOn(){
  if(!canPush)return Promise.resolve(false);
  return navigator.serviceWorker.register('/sw.js').then(function(){return navigator.serviceWorker.ready}).then(function(reg){
    return reg.pushManager.getSubscription().then(function(sub){
      if(sub)return sub;
      return fetch('/portal/bot/api/push/key',{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
        return reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:keyBytes(j.key)})});
    });
  }).then(function(sub){
    return fetch('/portal/bot/api/push/subscribe',{method:'POST',headers:H,body:JSON.stringify(sub.toJSON()),credentials:'same-origin'})
      .then(function(r){return r.ok});
  }).catch(function(){return false});
}
var perm=document.getElementById('perm'),st=document.getElementById('pushst');
function say(t){if(st)st.textContent=t}
function why(){
  if(!canPush)return /iPhone|iPad/.test(navigator.userAgent)?'На iPhone уведомления работают только в приложении с экрана «Домой»: «Поделиться» → «На экран „Домой“», откройте его оттуда и нажмите 🔔 ещё раз.':'Этот браузер не умеет уведомления для сайтов.';
  if(Notification.permission==='denied')return 'Уведомления запрещены. iPhone: Настройки → Уведомления → ЗК Админ → Разрешить. Затем нажмите 🔔 ещё раз.';
  return '';
}
function enable(e){
  if(e)e.preventDefault();
  var w=why();if(w){say(w);if(!st)alert(w);return}
  Notification.requestPermission().then(function(p){
    if(p!=='granted'){say(why()||'Разрешение не получено.');return}
    pushOn().then(function(ok){if(perm)perm.hidden=ok;say(ok?'Уведомления включены на этом устройстве ✅':'Не получилось включить уведомления: попробуйте ещё раз.')});
  });
}
if(perm){
  perm.onclick=enable;
  if(canPush&&Notification.permission==='granted')pushOn().then(function(ok){perm.hidden=ok});
  else perm.hidden=false;
}
var on=document.getElementById('pushon'),test=document.getElementById('pushtest');
if(on)on.onclick=enable;
if(test)test.onclick=function(e){
  e.preventDefault();say('Отправляю…');
  fetch('/portal/api/push/test',{method:'POST',headers:H,credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
    if(!j.total)say('Ни на одном устройстве уведомления не включены. Нажмите «Включить на этом устройстве».');
    else if(j.sent)say('Отправлено на устройств: '+j.sent+' из '+j.total+'. Если на телефоне ничего не появилось: Настройки → Уведомления → ЗК Админ.');
    else say('Не отправилось ('+(j.removed?'подписка устарела, нажмите «Включить на этом устройстве» ещё раз':'сервис уведомлений не ответил')+').');
  }).catch(function(){say('Нет связи с сервером.')});
};
})();</script>"""


def _seg(active: str) -> str:
    """Переключатель, который виден на каждой странице: Заявки · Клиенты · Исполнители (не прячем в меню)."""
    n = _NEW_N.get()
    cnt = f'<b class="cnt">{n}</b>' if n else ""
    items = (("orders", "/portal", "Заявки" + cnt), ("clients", "/portal/clients", "Клиенты"),
             ("executors", "/portal/executors", "Исполнители"), ("stats", "/portal/analytics", "📊"))
    return '<nav class="seg" aria-label="Разделы">' + "".join(
        f'<a {"class=\"on\" " if key == active else ""}href="{href}">{label}</a>' for key, href, label in items) + "</nav>"


# браузер владельца сообщает свой код посетителя сайта: аналитика стирает его прошлые заходы и больше не считает
OWN_VID_JS = ("<script>try{var zv=(JSON.parse(localStorage.getItem('zc_a')||'{}')).v;"
              "if(zv&&!sessionStorage.getItem('zc_own')){fetch('/portal/api/analytics/own',{method:'POST',credentials:'same-origin',"
              "headers:{'Content-Type':'application/json'},body:JSON.stringify({v:zv})}).then(function(){sessionStorage.setItem('zc_own','1')})}}catch(e){}</script>")


def page(title: str, body: str, active: str = "", flash: str | None = None, back: str | None = "/portal") -> str:
    nav = "".join(f'<a href="{href}" class="{"on" if key == active else ""}">{label}</a>' for key, href, label in (
        ("orders", "/portal", "Заявки"), ("clients", "/portal/clients", "Клиенты"),
        ("executors", "/portal/executors", "Исполнители"), ("stats", "/portal/analytics", "📊 Аналитика"),
        ("reports", "/portal/reports", "📥 Отчёты и база"),
        ("tariffs", "/portal/tariffs", "Тарифы"), ("settings", "/portal/settings", "Вход и почта"),
        ("bot", "/portal/bot", "Бот")))
    fl = f'<div class="flash">{_e(flash)}</div>' if flash else ""
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>{brand.NAME} — {_e(title)}</title>{brand.HEAD_ADMIN}<style>{CSS}</style></head><body>{ribbons.html(ribbons.ADMIN)}'
            f'<div class="top">{brand.LOGO}'
            f'<details class="menu"><summary>☰ Меню</summary><nav>{nav}<a href="#" id="perm" hidden>🔔 Уведомления</a><form method="post" action="/portal/as-client" class="asclient"><button>👤 Как клиент</button></form><a href="/portal/logout">Выйти</a></nav></details>{_seg(active)}</div>'
            f'<main>{brand.backrow(back) if back else ""}{fl}{body}</main><a id="newbar" class="btn" href="">.</a>{NOTIFY_JS}{OWN_VID_JS}</body></html>')


# ---------- Вход ----------

def _code_hash(tg_id: int, code: str) -> str:
    return hashlib.sha256(f"{tg_id}:{code}:{config.bot_token}".encode()).hexdigest()


async def _issue_code(tg_id: int, via: str) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    await db.execute("INSERT INTO login_codes (tg_id, code_hash, via, expires_at) VALUES (?,?,?, datetime('now','+10 minutes'))",
                     (tg_id, _code_hash(tg_id, code), via))
    return code


async def _check_code(code: str, via_in: tuple[str, ...] = ("tg", "email")) -> dict | None:
    rows = await db.fetch_all(
        "SELECT * FROM login_codes WHERE used=0 AND attempts<? AND expires_at > datetime('now')", (CODE_TRIES,))
    for r in rows:
        if r["via"] in via_in and secrets.compare_digest(r["code_hash"], _code_hash(r["tg_id"], code)):
            await db.execute(f"UPDATE login_codes SET used=1, {db.touch()} WHERE id=?", (r["id"],))
            return r
    # неверный код: каждая ошибка сжигает попытку у всех действующих кодов (перебор не поможет)
    await db.execute(f"UPDATE login_codes SET attempts=attempts+1, {db.touch()} WHERE used=0 AND expires_at > datetime('now')")
    return None


async def admin_email(tg_id: int) -> str | None:
    row = await db.fetch_one("SELECT value FROM kv WHERE key=?", (f"admin_email:{tg_id}",))
    if row:
        return row["value"]
    import os
    env = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
    return env if env and tg_id == min(config.admin_ids) else None


_PK_JS_COMMON = """
function d(s){s=s.replace(/-/g,'+').replace(/_/g,'/');while(s.length%4)s+='=';var b=atob(s),a=new Uint8Array(b.length);for(var i=0;i<b.length;i++)a[i]=b.charCodeAt(i);return a.buffer}
function e(buf){var a=new Uint8Array(buf),s='';for(var i=0;i<a.length;i++)s+=String.fromCharCode(a[i]);return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'')}
var H={'X-Zc':'1','Content-Type':'application/json'};
function say(t){var s=document.getElementById('pkst');if(s)s.textContent=t}
"""

# Общее для обеих кнопок. Главное для iPhone: Face ID вызывается СРАЗУ в нажатии (параметры подготовлены заранее),
# иначе Safari считает, что человек ничего не нажимал, и молча отказывает. Причину отказа показываем словами.
_PK_JS_COMMON += r"""
function why(err){var n=(err&&err.name)||'';
  if(n==='NotAllowedError')return 'Face ID не подтверждён или отменён. Нажмите кнопку ещё раз и посмотрите на телефон.';
  if(n==='InvalidStateError')return 'На этом устройстве ключ уже создан.';
  if(n==='SecurityError')return 'Браузер не разрешил ключ для этого адреса. Откройте портал по адресу https://… без переходов.';
  if(n==='NotSupportedError')return 'На этом устройстве нет Face ID для сайтов. В «Настройках» iPhone включите Face ID и код-пароль.';
  return 'Не получилось ('+(n||'ошибка')+'). Войдите по коду.'}
function report(where,err){try{fetch('/portal/passkey/err',{method:'POST',headers:H,credentials:'same-origin',
  body:JSON.stringify({where:where,name:(err&&err.name)||String(err||'')})})}catch(x){}}
function prep(url,fix){var box={pk:null};
  function load(){return fetch(url,{method:'POST',headers:H,credentials:'same-origin'}).then(function(r){return r.json()}).then(function(o){
    var pk=o.publicKey;pk.challenge=d(pk.challenge);if(fix)fix(pk);box.pk=pk;box.at=Date.now()}).catch(function(){box.pk=null})}
  box.load=load;box.take=function(){var pk=box.pk;box.pk=null;return(pk&&Date.now()-box.at<240000)?pk:null};
  load();setInterval(function(){if(!box.pk||Date.now()-box.at>200000)load()},30000);return box}
"""

# Кнопка на странице входа: вход по ключу, созданному в «Вход и почта». Показывается там, где браузер умеет ключи доступа.
LOGIN_PASSKEY_JS = ("<script>(function(){" + _PK_JS_COMMON + r"""
var b=document.getElementById('pklogin');
if(!b||!window.PublicKeyCredential||!navigator.credentials)return;
b.hidden=false;
var has=false;try{has=!!localStorage.getItem('zc_passkey')}catch(x){}
if(!has)b.textContent='Войти по Face ID (если включён на этом устройстве)';
var box=prep('/portal/passkey/options');
b.onclick=function(ev){
  ev.preventDefault();
  var pk=box.take();
  if(!pk){say('Готовлю вход… нажмите ещё раз через секунду.');box.load();return}
  say('Подтвердите Face ID или отпечаток…');
  navigator.credentials.get({publicKey:pk}).then(function(c){
    var r=c.response;
    return fetch('/portal/passkey/login',{method:'POST',headers:H,credentials:'same-origin',body:JSON.stringify({id:e(c.rawId),
      response:{clientDataJSON:e(r.clientDataJSON),authenticatorData:e(r.authenticatorData),signature:e(r.signature)}})});
  }).then(function(r){
    if(r.ok){location.href='/portal';return}
    box.load();
    say(r.status===429?'Слишком много попыток. Попробуйте позже или войдите по коду.':'Этот ключ не подошёл. Войдите по коду и включите Face ID заново в «Вход и почта».');
  }).catch(function(err){box.load();report('login',err);
    say(has?why(err):'На этом устройстве Face ID для портала ещё не включён: войдите по коду и включите в «Вход и почта».')});
};
})();</script>""")

# Карточка в «Вход и почта»: создать ключ на этом устройстве.
PASSKEY_JS = ("<script>(function(){" + _PK_JS_COMMON + r"""
var b=document.getElementById('pkon');
if(!b)return;
if(!window.PublicKeyCredential||!navigator.credentials){b.hidden=true;say('Этот браузер не умеет Face ID для сайтов. На iPhone откройте портал в Safari или в приложении с экрана «Домой».');return}
var box=prep('/portal/api/passkey/options',function(pk){pk.user.id=d(pk.user.id);(pk.excludeCredentials||[]).forEach(function(c){c.id=d(c.id)})});
b.onclick=function(ev){
  ev.preventDefault();
  var pk=box.take();
  if(!pk){say('Готовлю ключ… нажмите ещё раз через секунду.');box.load();return}
  say('Подтвердите Face ID или отпечаток…');
  navigator.credentials.create({publicKey:pk}).then(function(c){
    var r=c.response;
    return fetch('/portal/api/passkey/register',{method:'POST',headers:H,credentials:'same-origin',body:JSON.stringify({id:e(c.rawId),
      response:{clientDataJSON:e(r.clientDataJSON),attestationObject:e(r.attestationObject)}})});
  }).then(function(r){
    if(!r.ok){box.load();say('Сервер не принял ключ. Нажмите кнопку ещё раз.');return}
    try{localStorage.setItem('zc_passkey','1')}catch(x){}
    say('Face ID включён на этом устройстве ✅');setTimeout(function(){location.reload()},900);
  }).catch(function(err){box.load();report('register',err);say(why(err))});
};
})();</script>""")


def login_page(msg: str | None = None, code_form: bool = False) -> str:
    tg = f"https://t.me/{config.bot_username}?start=webadmin"
    fl = f'<div class="flash">{_e(msg)}</div>' if msg else ""
    code = ('<form method="post" action="/portal/verify" class="card" style="padding:16px;margin-bottom:12px">'
            '<label for="code">Код из 6 цифр</label><input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" '
            'maxlength="6" pattern="[0-9]{6}" required style="width:100%;font-size:22px;letter-spacing:.2em">'
            '<button class="btn" style="width:100%;margin-top:10px">Войти</button></form>') if code_form else ""
    return (f'<!doctype html><html lang="ru"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{brand.NAME} — вход</title>'
            f'{brand.HEAD_ADMIN}<style>{CSS}</style></head><body>{ribbons.html(ribbons.ADMIN)}<main style="max-width:460px">'
            f'{brand.backrow("/")}'
            f'<div style="text-align:center;font-size:26px;margin:18px 0 6px">{brand.LOGO}</div>'
            f'<h1 style="text-align:center">Вход в портал</h1>{fl}{code}'
            f'<div class="card" style="padding:16px;margin-bottom:12px"><button class="btn" id="pklogin" hidden style="width:100%">Войти по Face ID</button>'
            f'<p id="pkst" class="muted" style="margin:0"></p></div>'
            f'<div class="card" style="padding:16px;margin-bottom:12px"><b>Через Telegram</b>'
            f'<p class="muted">Откроется бот и пришлёт ссылку для входа. Или получите код:</p>'
            f'<a class="btn" style="width:100%" href="{_e(tg)}">Войти через Telegram</a>'
            f'<form method="post" action="/portal/code" style="margin-top:8px"><input type="hidden" name="via" value="tg">'
            f'<button class="btn ghost" style="width:100%">Прислать код в Telegram</button></form>'
            f'<p class="muted" style="font-size:14px">Открыли портал с экрана «Домой» на iPhone? Нажмите «Прислать код '
            f'в Telegram» и введите код здесь: ссылка из Telegram откроется в Safari, а не в этом приложении.</p></div>'
            f'<div class="card" style="padding:16px"><b>Без Telegram и VPN — код на почту</b>'
            f'<form method="post" action="/portal/code"><input type="hidden" name="via" value="email">'
            f'<label for="email">Ваша почта</label><input id="email" name="email" type="email" autocomplete="email" required style="width:100%">'
            f'<button class="btn ghost" style="width:100%;margin-top:10px">Прислать код на почту</button></form>'
            f'<p class="muted" style="font-size:14px">Почту для входа добавляют в портале: «Вход и почта».</p></div>'
            f'</main>{LOGIN_PASSKEY_JS}</body></html>')


async def post_code(request: web.Request) -> web.Response:
    form = await request.post()
    via = str(form.get("via", ""))
    if _limit(_hits, _ip(request), CODE_LIMIT, 600):
        return _html_resp(login_page("Слишком много запросов кода. Подождите 10 минут.", code_form=True))
    bot = request.app["bot"]
    if via == "tg":
        for admin_id in config.admin_ids:
            code = await _issue_code(admin_id, "tg")
            await orders.notify(bot, admin_id, f"🔐 Код входа в портал: {code}\nДействует 10 минут. "
                                               "Если вход не ваш — ничего не делайте, код сгорит сам.")
        return _html_resp(login_page("Код отправлен вам в Telegram.", code_form=True))
    if via == "email":
        if not mailer.configured():
            return _html_resp(login_page("Почта для входа не настроена. Войдите через Telegram и откройте «Вход и почта» — "
                                         "там написано, что добавить в Railway."))
        email = str(form.get("email", "")).strip().lower()[:200]
        for admin_id in config.admin_ids:
            if email and await admin_email(admin_id) == email:
                code = await _issue_code(admin_id, "email")
                await mailer.send(email, f"{brand.NAME}: код входа {code}",
                                  f"Код входа в портал {brand.NAME}: {code}\nДействует 10 минут.\n"
                                  "Если вход не ваш — просто удалите письмо.")
        return _html_resp(login_page("Если адрес зарегистрирован, код уже в почте (проверьте и «Спам»).", code_form=True))
    return _html_resp(login_page())


async def post_verify(request: web.Request) -> web.Response:
    if _limit(_fails, _ip(request), FAIL_LIMIT, 3600):
        return _html_resp(login_page("Слишком много попыток. Попробуйте через час.", code_form=True))
    code = re.sub(r"\D", "", str((await request.post()).get("code", "")))
    row = await _check_code(code) if len(code) == 6 else None
    if not row or row["tg_id"] not in config.admin_ids:
        return _html_resp(login_page("Код не подошёл или устарел. Запросите новый.", code_form=True))
    _fails.pop(_ip(request), None)
    token = await webchat.new_session(row["tg_id"], device=webchat.device_label(request.headers.get("User-Agent")))
    # первый вход по коду: сразу предлагаем Face ID одной кнопкой (как у «Моего помощника»), если ключей ещё нет
    nxt = "/portal" if await passkeys.list_for(row["tg_id"]) else "/portal/face"
    resp = web.Response(status=303, headers={"Location": nxt})
    webchat._set_cookie(request, resp, token, admin=True)
    return resp


@admin_page
async def face_page(request: web.Request, uid: int) -> web.Response:
    """Одна кнопка: включить Face ID на этом устройстве. Если ключ здесь уже создан — сразу в портал."""
    skip = ('<script>try{if(localStorage.getItem("zc_passkey")&&!location.search)location.replace("/portal")}'
            'catch(x){}</script>')
    body = (f'{skip}<h1>Вход по Face ID</h1><div class="card" style="padding:16px">'
            f'<p>В следующий раз не нужен код из Telegram или почты: телефон узнает вас по лицу или отпечатку.</p>'
            f'<button class="btn" id="pkon" type="button" style="width:100%;font-size:18px">Включить Face ID</button>'
            f'<p id="pkst" class="muted" style="margin-top:8px"></p>'
            f'<a class="btn ghost" href="/portal" style="width:100%;margin-top:8px;text-align:center">Не сейчас</a></div>{PASSKEY_JS}')
    return _html_resp(page("Face ID", body, "settings", back=None))


async def logout(request: web.Request) -> web.Response:
    token = request.cookies.get(webchat.ADMIN_COOKIE)
    if token:
        await db.execute("DELETE FROM web_sessions WHERE token_hash=?", (webchat._hash(token),))
        webchat._linked_cache.clear()
        webchat._targets_cache.clear()
    resp = web.Response(status=303, headers={"Location": "/portal"})
    resp.del_cookie(webchat.ADMIN_COOKIE, path="/")
    return resp


# ---------- Заявки ----------

def _source(tg_id: int) -> str:
    return "web" if tg_id < 0 else "tg"


async def all_requests(flt: str, src: str) -> list[dict]:
    statuses = {"new": ("created",), "active": orders.ACTIVE_STATUSES}.get(flt)
    where, params = "", []
    if statuses:
        where = f"WHERE o.status IN ({','.join('?' * len(statuses))})"
        params = list(statuses)
        if flt == "active":  # отменённые и выполненные за последние сутки не пропадают сразу: админ видит, что случилось
            where = ("WHERE (" + where[6:] + " OR o.id IN (SELECT order_id FROM order_statuses_log WHERE to_status IN "
                     "('cancelled','completed') AND created_at > datetime('now','-1 day')))")
    rows = []
    if src in ("all", "tg", "web"):
        for o in await db.fetch_all(
                f"""SELECT o.id, o.status, o.start_at, o.created_at, o.price_total, s.name AS service, s.category,
                           u.name, u.phone, u.tg_id,
                           (SELECT actor FROM order_statuses_log l WHERE l.order_id=o.id AND l.to_status='cancelled'
                            ORDER BY l.id DESC LIMIT 1) AS cancel_actor
                    FROM orders o JOIN services s ON s.id=o.service_id
                    JOIN users u ON u.id=o.user_id {where} ORDER BY o.id DESC LIMIT 300""", tuple(params)):
            if src != "all" and _source(o["tg_id"]) != src:
                continue
            rows.append({"kind": "order", **o, "source": _source(o["tg_id"])})
    if src in ("all", "form"):
        rs = {"new": ("new",), "active": ("new", "work")}.get(flt)
        w = f"WHERE r.status IN ({','.join('?' * len(rs))})" if rs else ""
        for r in await db.fetch_all(
                f"""SELECT r.*, s.name AS service, s.category FROM web_requests r LEFT JOIN services s ON s.id=r.service_id
                    {w} ORDER BY r.id DESC LIMIT 300""", rs or ()):
            rows.append({"kind": "form", **r, "source": "form"})
    rows.sort(key=lambda r: (r["created_at"], r["id"]), reverse=True)
    return rows


def _local_dt(value):
    return to_local(from_db(value)) if isinstance(value, str) else to_local(value)


def stamp_html(created_at, fresh: bool = False) -> str:
    """Крупно: время регистрации заявки, под ним дата. Новые заявки — коралловым, чтобы бросались в глаза."""
    d = _local_dt(created_at)
    return f'<span class="stamp{" new" if fresh else ""}"><b>{d:%H:%M}</b><span>{d:%d.%m}</span></span>'


def day_label(created_at) -> str:
    d = _local_dt(created_at)
    today = to_local(now_utc()).date()
    base = f"{d.day} {MONTHS_GEN[d.month - 1]}, {WEEKDAYS_RU[d.weekday()]}"
    gap = (today - d.date()).days
    return ("Сегодня · " if gap == 0 else "Вчера · " if gap == 1 else "") + base


def reg_html(created_at, what: str) -> str:
    """Плашка наверху карточки: когда заявка зарегистрирована (время и дата — крупно)."""
    d = _local_dt(created_at)
    return f'<div class="reg"><b>{d:%H:%M}</b><span>{d:%d.%m.%Y}</span><em>{_e(what)}</em></div>'


def _row_html(r: dict) -> str:
    chip = f'<span class="chip {CHIP.get(r.get("category"), "walk")}">{_e(r.get("service") or "Услуга")}</span>'
    src = f'<span class="src">{SOURCES[r["source"]]}</span>'
    if r["kind"] == "order":
        label = cancel_label(r.get("cancel_actor")) if r["status"] == "cancelled" else texts.STATUS_LABELS[r["status"]]
        st = f'<span class="badge" style="background:{STATUS_COLOR[r["status"]]}">{_e(label)}</span>'
        if r["status"] == "completed":
            st = TICK + st
        fresh = r["status"] == "created"
        return (f'<a class="item card ord {STATUS_CLASS[r["status"]]}" href="/portal/order/{r["id"]}">{stamp_html(r["created_at"], fresh)}<div class="main">'
                f'<div class="row"><span class="who">№{r["id"]} · {_e(r["name"] or "—")}</span><span class="stt">{st}</span></div>'
                f'<div class="row" style="margin-top:6px">{chip}{src}</div>'
                f'<div class="sub">На {_e(fmt_local(r["start_at"]))} · {_e(r["phone"] or "—")} · {rub(r["price_total"])}</div></div></a>')
    st = f'<span class="badge" style="background:{"var(--coral)" if r["status"] == "new" else "#8a8178"}">{REQ_STATUSES.get(r["status"], r["status"])}</span>'
    if r["status"] == "done":
        st = TICK + st
    if str(r.get("address") or "").startswith("Сайт "):  # заявка с сайта конструктора: показываем, с какого
        src = f'<span class="src">{_e(r["address"])}</span>'
    return (f'<a class="item card ord {REQ_CLASS.get(r["status"], "st-new")}" href="/portal/request/{r["id"]}">{stamp_html(r["created_at"], r["status"] == "new")}<div class="main">'
            f'<div class="row"><span class="who">Заявка с сайта №{r["id"]} · {_e(r["name"])}</span><span class="stt">{st}</span></div>'
            f'<div class="row" style="margin-top:6px">{chip}{src}</div>'
            f'<div class="sub">{_e(r["when_text"] or "время не указано")} · {_e(r["phone"])}</div></div></a>')


def _grouped(rows: list[dict]) -> str:
    """Строки заявок, разбитые по дням: «Сегодня · 8 ноября, воскресенье» и так далее."""
    out, day = [], None
    for r in rows:
        label = day_label(r["created_at"])
        if label != day:
            out.append(f'<h3 class="day">{_e(label)}</h3>')
            day = label
        out.append(_row_html(r))
    return "".join(out)


@admin_page
async def list_page(request: web.Request, uid: int) -> web.Response:
    flt = request.query.get("f", "active")   # по умолчанию — только новые и в работе
    flt = flt if flt in ("new", "active", "all") else "active"
    src = request.query.get("src", "all")
    src = src if src in ("all", "tg", "web", "form") else "all"
    rows = await all_requests(flt, src)
    tabs = "".join(f'<a class="{"on" if k == flt else ""}" href="/portal?f={k}&src={src}">{v}</a>'
                   for k, v in (("new", "Новые"), ("active", "В работе"), ("all", "Все")))
    stabs = "".join(f'<a class="{"on" if k == src else ""}" href="/portal?f={flt}&src={k}">{v}</a>'
                    for k, v in (("all", "Все источники"), *SOURCES.items()))
    changed = (flt, src) != ("active", "all")
    hint = {"new": "только новые", "active": "новые и в работе", "all": "все"}[flt] + ("" if src == "all" else f" · {SOURCES[src]}")
    flt_html = (f'<details class="filter"{" open" if changed else ""}><summary>🔍 Фильтр<small class="muted"> · {hint}</small></summary>'
                f'<div class="tabs" style="margin-top:10px">{tabs}</div><div class="tabs">{stabs}</div></details>')
    body = (f'<h1>Заявки</h1>{flt_html}'
            f'<div class="list">{_grouped(rows) or "<p class=muted>Пока пусто.</p>"}</div>')
    return _html_resp(page("Заявки", body, "orders", request.query.get("m"), back=None))


def _actions(o: dict, execs: list[dict], room_open: bool) -> str:
    oid = o["id"]

    def btn(action, label, cls="", extra=""):
        return (f'<form method="post" action="/portal/order/{oid}"><input type="hidden" name="action" value="{action}">'
                f'{extra}<button class="btn small {cls}">{label}</button></form>')
    st = o["status"]
    out = []
    if st == "created":
        out.append(btn("confirm", "✅ Подтвердить"))
    if st == "confirmed":
        out.append(btn("link", "💳 Отправить ссылку", extra='<input name="link" type="url" placeholder="https://…" required>'))
        if payinfo.methods(oid):
            out.append(btn("methods", "СБП / карта", "ghost"))
        out.append(btn("paid", "💰 Оплата пришла"))
    if st == "paid":
        opts = "".join(f'<option value="{e["id"]}">{_e(e["name"])}</option>' for e in execs)
        out.append(btn("assign", "👷 Назначить", extra=f'<select name="executor_id" required>{opts}</select>') if execs
                   else '<span class="muted">Нет подходящих исполнителей — добавьте в «Исполнители».</span>')
    if st == "assigned":
        out.append(btn("progress", "▶️ В работе"))
    if st == "in_progress":
        out.append(btn("complete", "🏁 Выполнен"))
    if st in ("assigned", "in_progress") and o.get("executor_tg_id"):
        out.append(btn("room_close", "Закрыть чат клиента и исполнителя", "ghost") if room_open
                   else btn("room_open", "💬 Открыть чат клиента и исполнителя", "ghost"))
    if st not in ("completed", "cancelled"):
        out.append(btn("cancel", "Отменить", "danger", extra='<input name="reason" placeholder="причина" maxlength="200">'))
    return f'<div class="actions">{"".join(out)}</div>'


def _pager(prev_href: str | None, prev_label: str, mid_href: str | None, mid_label: str,
           next_href: str | None, next_label: str) -> str:
    def cell(href, label):
        return f'<a href="{href}">{label}</a>' if href else f'<span class="off">{label}</span>'
    return f'<div class="pager">{cell(prev_href, prev_label)}{cell(mid_href, mid_label)}{cell(next_href, next_label)}</div>'


async def _order_nav(o: dict) -> str:
    """Соседние заказы по номеру, переход к клиенту и другие заказы этого же клиента: не нужно возвращаться в список."""
    prev = await db.fetch_one("SELECT MAX(id) AS n FROM orders WHERE id<?", (o["id"],))
    nxt = await db.fetch_one("SELECT MIN(id) AS n FROM orders WHERE id>?", (o["id"],))
    p, n = prev["n"], nxt["n"]
    pager = _pager(f"/portal/order/{p}" if p else None, f"← №{p}" if p else "←",
                   f"/portal/chat/{o['client_tg_id']}", "👤 Клиент",
                   f"/portal/order/{n}" if n else None, f"№{n} →" if n else "→")
    rest = await db.fetch_all("SELECT id, status, created_at FROM orders WHERE user_id=? AND id<>? ORDER BY id DESC LIMIT 8",
                              (o["user_id"], o["id"]))
    chips = "".join(f'<a class="src" href="/portal/order/{x["id"]}">№{x["id"]} · {_local_dt(x["created_at"]):%d.%m %H:%M}'
                    f' · {_e(texts.STATUS_LABELS[x["status"]])}</a>' for x in rest)
    others = f'<div class="muted" style="font-size:14px;margin:0 0 4px">Другие заказы клиента</div><div class="others">{chips}</div>' if chips else ""
    return pager + others


@admin_page
async def order_page(request: web.Request, uid: int) -> web.Response:
    oid = request.match_info["oid"]
    o = await orders.get_order_full(int(oid)) if oid.isdigit() else None
    if not o:
        return _html_resp(page("Не найдено", "<h1>Заказ не найден</h1>"), 404)
    execs = [e for e in await db.fetch_all("SELECT id, name, services FROM executors WHERE active=1 ORDER BY name")
             if o["category"] in (e["services"] or "").split(",")]
    room = await db.fetch_one("SELECT open FROM rooms WHERE order_id=?", (o["id"],))
    room_open = bool(room and room["open"])
    log = await talk.room_log(o["id"])
    room_html = ""
    if log or room_open:
        msgs = "".join(f'<div class="msg room"><b>{"Клиент" if m["party_kind"] == "client" else "Исполнитель"}:</b> '
                       f'{_e(m["text"])}<span class="t">{_e(fmt_local(m["created_at"]))}</span></div>' for m in log)
        room_html = (f'<h2>Чат клиента и исполнителя {"(открыт)" if room_open else "(закрыт)"}</h2>'
                     f'<div class="card msgs">{msgs or "<span class=muted>Сообщений пока нет.</span>"}</div>')
    hist = await db.fetch_all("SELECT * FROM order_statuses_log WHERE order_id=? ORDER BY id", (o["id"],))
    hist_html = "".join(f'<div class="sub muted">{_e(fmt_local(h["created_at"]))}: {_e(texts.STATUS_LABELS.get(h["to_status"], h["to_status"]))}'
                        f' · {_e(h["actor"])}'
                        + (f'<br><span class="muted">{_e(h["note"])}</span>' if h.get("note") else "")
                        + '</div>' for h in hist)
    links = [f'<a class="btn small ghost" href="/portal/chat/{o["client_tg_id"]}">💬 Написать клиенту</a>']
    if o.get("client_phone"):
        links.append(f'<a class="btn small ghost" href="tel:{_e(o["client_phone"])}">📞 Позвонить</a>')
    if o.get("executor_tg_id"):
        links.append(f'<a class="btn small ghost" href="/portal/chat/{o["executor_tg_id"]}">💬 Написать исполнителю</a>')
    nav_html = await _order_nav(o)
    reg = reg_html(o["created_at"], f"Заявка №{o['id']} зарегистрирована")
    who_cancel = next((h["actor"] for h in reversed(hist) if h["to_status"] == "cancelled"), None)
    st_text = cancel_label(who_cancel) if o["status"] == "cancelled" else texts.STATUS_LABELS[o["status"]]
    stbar = f'<div class="stbar {STATUS_CLASS[o["status"]]}">{TICK if o["status"] == "completed" else ""}<span>{_e(st_text)}</span></div>'
    body = (f'<h1>Заказ №{o["id"]} <span class="src">{SOURCES[_source(o["client_tg_id"])]}</span></h1>'
            f'{stbar}{reg}{nav_html}'
            f'<div class="card pre">{_e(orders.order_card(o, for_staff=True))}</div>'
            f'{_actions(o, execs, room_open)}<div class="actions">{"".join(links)}</div>{room_html}'
            f'<h2>История</h2>{hist_html}')
    return _html_resp(page(f"Заказ №{o['id']}", body, "orders", request.query.get("m")))


_URL = re.compile(r"^https?://\S{3,500}$")


@admin_page
async def order_action(request: web.Request, uid: int) -> web.Response:
    oid_s = request.match_info["oid"]
    if not oid_s.isdigit():
        return web.Response(status=404)
    oid, bot = int(oid_s), request.app["bot"]
    form = await request.post()
    action = str(form.get("action", ""))
    actor = f"portal:{uid}"
    from handlers import admin as admin_h
    steps = {"confirm": "confirmed", "paid": "paid", "progress": "in_progress", "complete": "completed"}
    if action in steps:
        ok, msg = await orders.change_status(bot, oid, steps[action], actor)
        if ok and action == "confirm":
            await admin_h._offer_methods(bot, oid)
    elif action == "cancel":
        reason = str(form.get("reason", "")).strip()[:200] or "отменён администратором"
        ok, msg = await orders.change_status(bot, oid, "cancelled", actor, reason)
    elif action == "assign" and str(form.get("executor_id", "")).isdigit():
        ok, msg = await orders.assign_executor(bot, oid, int(form["executor_id"]), actor)
    elif action == "link":
        link = str(form.get("link", "")).strip()
        ok, msg = (await orders.send_payment(bot, oid, link, actor)) if _URL.match(link) else (False, "Нужна ссылка вида https://…")
    elif action == "methods":
        ok = await admin_h._offer_methods(bot, oid)
        msg = "Способы оплаты отправлены клиенту." if ok else "Реквизиты не настроены (Railway → Variables)."
    elif action == "room_open":
        ok, msg = await talk.open_room(bot, oid)
    elif action == "room_close":
        ok, msg = await talk.close_room(bot, oid)
    else:
        msg = "Неизвестное действие."
    return _redirect(f"/portal/order/{oid}", msg)


@admin_page
async def request_page(request: web.Request, uid: int) -> web.Response:
    rid = request.match_info["rid"]
    r = await db.fetch_one("SELECT r.*, s.name AS service FROM web_requests r LEFT JOIN services s ON s.id=r.service_id WHERE r.id=?",
                           (int(rid),)) if rid.isdigit() else None
    if not r:
        return _html_resp(page("Не найдено", "<h1>Заявка не найдена</h1>"), 404)
    info = "\n".join([f"Услуга: {r['service'] or '—'}", f"Когда: {r['when_text'] or '—'}", f"Адрес: {r['address']}",
                      f"Питомец: {r['pet'] or '—'}", f"Комментарий: {r['comment'] or '—'}", f"Имя: {r['name']}",
                      f"Телефон: {r['phone']}", f"Зарегистрирована: {fmt_local(r['created_at'])}"])
    opts = "".join(f'<option value="{k}"{" selected" if k == r["status"] else ""}>{v}</option>' for k, v in REQ_STATUSES.items())
    site = r["address"] if str(r["address"]).startswith("Сайт ") else "Форма на сайте"
    if r["status"] == "rejected":
        cancel = ('<form method="post" class="card" style="padding:14px"><p><b>❌ Заявка отменена.</b></p>'
                  '<button class="btn small" name="restore" value="1">↩️ Вернуть в работу</button></form>')
    else:
        cancel = ('<form method="post" class="card" style="padding:14px" onsubmit="return confirm(\'Отменить эту заявку?\')">'
                  '<label for="cnote">Причина (по желанию)</label>'
                  '<input id="cnote" name="note" maxlength="1000" style="width:100%">'
                  '<button class="btn small" name="cancel" value="1" style="margin-top:10px;background:#d62b2b">❌ Отменить заявку</button></form>')
    who = [f"Устройство: {r['device'] or '—'}", f"IP: {r['ip'] or '—'}"]
    same = await db.fetch_all("SELECT id, device, ip, created_at FROM web_requests WHERE phone=? AND id<>? ORDER BY id DESC LIMIT 10",
                              (r["phone"], r["id"]))
    others = "".join(f'<a class="src" href="/portal/request/{x["id"]}">№{x["id"]} · {_e(x["device"] or "—")} · {_e(x["ip"] or "—")}</a> '
                     for x in same)
    same_html = (f'<h2>Другие заявки с этого номера</h2><div class="chips">{others}</div>'
                 f'<p class="muted" style="font-size:14px">Устройство и IP помогают отличить клиентов: у каждого браузера свой чат.</p>') if same else ""
    chat = (f'<a class="btn small" href="/portal/chat/{r["web_uid"]}">💬 Чат с клиентом</a>' if r["web_uid"] else "")
    pv = await db.fetch_one("SELECT MAX(id) AS n FROM web_requests WHERE id<?", (r["id"],))
    nx = await db.fetch_one("SELECT MIN(id) AS n FROM web_requests WHERE id>?", (r["id"],))
    pn, nn = pv["n"], nx["n"]
    pager = _pager(f"/portal/request/{pn}" if pn else None, f"← №{pn}" if pn else "←",
                   f"/portal/chat/{r['web_uid']}" if r["web_uid"] else None, "👤 Клиент",
                   f"/portal/request/{nn}" if nn else None, f"№{nn} →" if nn else "→")
    reg = reg_html(r["created_at"], f"Заявка с сайта №{r['id']} зарегистрирована")
    body = (f'<h1>Заявка с сайта №{r["id"]} <span class="src">{_e(site)}</span></h1>{reg}{pager}<div class="card pre">{_e(info)}\n{_e(chr(10).join(who))}</div>'
            f'<div class="actions">{chat}<a class="btn small" href="tel:{_e(r["phone"])}">📞 Позвонить</a>'
            f'<a class="btn small ghost" href="sms:{_e(r["phone"])}">✉️ СМС</a></div>'
            f'<form method="post" class="card" style="padding:14px"><label for="status">Статус</label><select id="status" name="status">{opts}</select>'
            f'<label for="note">Заметка</label><textarea id="note" name="note" maxlength="1000" style="width:100%;min-height:80px">{_e(r["admin_note"])}</textarea>'
            f'<button class="btn" style="margin-top:10px">Сохранить</button></form>' + cancel + same_html)
    return _html_resp(page(f"Заявка №{r['id']}", body, "orders", request.query.get("m")))


@admin_page
async def request_action(request: web.Request, uid: int) -> web.Response:
    rid = request.match_info["rid"]
    if not rid.isdigit():
        return web.Response(status=404)
    form = await request.post()
    if form.get("cancel") or form.get("restore"):  # отмена и возврат одной кнопкой
        if form.get("cancel"):
            await db.execute(f"UPDATE web_requests SET status='rejected', admin_note=COALESCE(?, admin_note), {db.touch()} WHERE id=?",
                             (str(form.get("note", "")).strip()[:1000] or None, int(rid)))
            return _redirect(f"/portal/request/{rid}", "Заявка отменена.")
        await db.execute(f"UPDATE web_requests SET status='new', {db.touch()} WHERE id=? AND status='rejected'", (int(rid),))
        return _redirect(f"/portal/request/{rid}", "Заявка возвращена в работу.")
    status = str(form.get("status", ""))
    if status not in REQ_STATUSES:
        return _redirect(f"/portal/request/{rid}", "Неизвестный статус.")
    await db.execute(f"UPDATE web_requests SET status=?, admin_note=?, {db.touch()} WHERE id=?",
                     (status, str(form.get("note", "")).strip()[:1000] or None, int(rid)))
    return _redirect(f"/portal/request/{rid}", "Сохранено.")


# ---------- Клиенты, исполнители, переписка ----------

@admin_page
async def clients_page(request: web.Request, uid: int) -> web.Response:
    q = request.query.get("q", "").strip()[:60]
    like = f"%{q}%"
    rows = await db.fetch_all(
        """SELECT u.*, (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) AS n,
                  (SELECT MAX(id) FROM messages m WHERE m.party_tg_id=u.tg_id) AS last_msg,
                  (SELECT text FROM messages m WHERE m.party_tg_id=u.tg_id ORDER BY id DESC LIMIT 1) AS last_text,
                  (SELECT COUNT(*) FROM web_requests r WHERE r.web_uid=u.tg_id) AS nreq,
                  (SELECT o.id FROM orders o WHERE o.user_id=u.id ORDER BY o.id DESC LIMIT 1) AS last_order,
                  (SELECT o.created_at FROM orders o WHERE o.user_id=u.id ORDER BY o.id DESC LIMIT 1) AS last_order_at
           FROM users u WHERE (u.pdn_consent=1 OR EXISTS (SELECT 1 FROM orders o WHERE o.user_id=u.id)
                               OR EXISTS (SELECT 1 FROM messages m WHERE m.party_tg_id=u.tg_id)
                               OR EXISTS (SELECT 1 FROM web_requests r WHERE r.web_uid=u.tg_id))
             AND (?='' OR u.name LIKE ? OR u.phone LIKE ?) ORDER BY COALESCE(last_msg,0) DESC, u.id DESC LIMIT 300""",
        (q, like, like))
    items = []
    for u in rows:
        ok = u["tg_id"] > 0 or u["verified_tg_id"]
        mark = '<span class="badge" style="background:var(--nanny)">проверен Telegram</span>' if ok else \
               '<span class="badge" style="background:#b9b0a6">не подтверждён</span>'
        last = f'<div class="sub">💬 {_e(str(u["last_text"] or "")[:80])}</div>' if u["last_text"] else ""
        if u["last_order"]:
            last = (f'<div class="sub">Последний заказ №{u["last_order"]} · <b>{_local_dt(u["last_order_at"]):%d.%m %H:%M}</b></div>' + last)
        items.append(f'<a class="item card" href="/portal/chat/{u["tg_id"]}"><div class="row"><span class="who">{_e(u["name"] or "Без имени")}</span>{mark}</div>'
                     f'<div class="row" style="margin-top:6px"><span class="src">{SOURCES[_source(u["tg_id"])]}</span></div>'
                     f'<div class="sub">{_e(u["phone"] or "—")} · заказов: {u["n"]} · заявок с сайта: {u["nreq"]}</div>{last}</a>')
    body = (f'<h1>Клиенты</h1><p><a class="btn small" href="/portal/export/database.xlsx">📥 Выгрузить базу в Excel</a></p>'
            f'<form class="send" method="get"><input name="q" value="{_e(q)}" placeholder="имя или телефон">'
            f'<button class="btn small">Найти</button></form><div class="list" style="margin-top:10px">{"".join(items) or "<p class=muted>Никого.</p>"}</div>')
    return _html_resp(page("Клиенты", body, "clients"))


@admin_page
async def executors_page(request: web.Request, uid: int) -> web.Response:
    rows = await db.fetch_all("SELECT * FROM executors ORDER BY active DESC, name")
    items = []
    for e in rows:
        cats = "".join(f'<span class="chip {CHIP.get(c, "walk")}">{_e(texts.CATEGORY_TITLES.get(c, c))}</span>'
                       for c in (e["services"] or "").split(",") if c)
        link = f'/portal/chat/{e["tg_id"]}' if e["tg_id"] else "#"
        items.append(f'<a class="item card" href="{link}"><div class="row"><span class="who">{_e(e["name"])}</span>'
                     f'<span class="badge" style="background:{"var(--nanny)" if e["active"] else "#b9b0a6"}">{"активен" if e["active"] else "отключён"}</span></div>'
                     f'<div class="chips" style="margin-top:6px">{cats}</div><div class="sub">{_e(e["phone"] or "—")}'
                     f'{"" if e["tg_id"] else " · нет Telegram — писать некуда"}</div></a>')
    body = (f'<h1>Исполнители</h1><p class="muted">Добавить исполнителя: команда /add_executor в боте. Отдельный бот для '
            f'исполнителей подключится сюда же.</p><div class="list">{"".join(items) or "<p class=muted>Пока нет.</p>"}</div>')
    return _html_resp(page("Исполнители", body, "executors"))


CHAT_JS = """<script>(function(){
var box=document.getElementById('msgs'),last=0,tg=box.dataset.tg;
function add(m){var d=document.createElement('div');d.className='msg '+m.direction;var t=document.createElement('span');
 t.textContent=m.text;d.appendChild(t);var s=document.createElement('span');s.className='t';
 s.textContent=(m.direction==='room'?'в общем чате №'+m.order_id+' · ':'')+m.created_at+' UTC';d.appendChild(s);box.appendChild(d)}
function load(){fetch('/portal/api/chat/'+tg+'?after='+last,{credentials:'same-origin'}).then(function(r){return r.json()}).then(function(j){
 var near=box.scrollHeight-box.scrollTop-box.clientHeight<80;(j.messages||[]).forEach(function(m){add(m);last=Math.max(last,m.id)});
 if(near||!box.dataset.init){box.scrollTop=box.scrollHeight;box.dataset.init=1}}).catch(function(){})}
document.getElementById('sf').onsubmit=function(e){e.preventDefault();var i=document.getElementById('st'),v=i.value.trim();if(!v)return;i.value='';
 fetch('/portal/api/chat/'+tg,{method:'POST',credentials:'same-origin',headers:{'X-Zc':'1','Content-Type':'application/json'},body:JSON.stringify({text:v})}).then(load)};
load();setInterval(load,4000);})();</script>"""


@admin_page
async def chat_page(request: web.Request, uid: int) -> web.Response:
    tg_s = request.match_info["tg"]
    if not re.fullmatch(r"-?\d{1,15}", tg_s):
        return web.Response(status=404)
    tg = int(tg_s)
    kind, name = await talk.party(tg)
    who = "Исполнитель" if kind == "executor" else "Клиент"
    ords = await db.fetch_all("SELECT o.id, o.status FROM orders o JOIN users u ON u.id=o.user_id WHERE u.tg_id=? ORDER BY o.id DESC LIMIT 10", (tg,))
    ol = " ".join(f'<a class="src" href="/portal/order/{o["id"]}">№{o["id"]} · {_e(texts.STATUS_LABELS[o["status"]])}</a>' for o in ords)
    body = (f'<h1>{who}: {_e(name)}</h1><div class="chips">{ol}</div>'
            f'<p class="muted" style="font-size:14px">Сообщения уходят от имени бота: в Telegram или в чат на сайте — туда, где клиент.</p>'
            f'<div id="msgs" class="card msgs" data-tg="{tg}"></div>'
            f'<form id="sf" class="send"><input id="st" maxlength="2000" placeholder="Сообщение…" autocomplete="off"><button class="btn small">➤</button></form>'
            f'{CHAT_JS}')
    return _html_resp(page(f"{who} {name}", body, "clients" if kind == "client" else "executors"))


@admin_page
async def api_chat(request: web.Request, uid: int) -> web.Response:
    tg_s = request.match_info["tg"]
    if not re.fullmatch(r"-?\d{1,15}", tg_s):
        return web.json_response({"error": "id"}, status=404)
    tg = int(tg_s)
    if request.method == "POST":
        if request.headers.get("X-Zc") != "1":
            return web.json_response({"error": "csrf"}, status=403)
        try:
            text = str((await request.json()).get("text") or "").strip()
        except Exception:
            return web.json_response({"error": "json"}, status=400)
        if not text:
            return web.json_response({"error": "empty"}, status=400)
        ok = await talk.admin_say(request.app["bot"], tg, text, author=f"portal:{uid}")
        return web.json_response({"ok": ok})
    try:
        after = int(request.query.get("after", "0"))
    except ValueError:
        after = 0
    rows = await talk.thread(tg, after)
    return web.json_response({"messages": [{k: r[k] for k in ("id", "direction", "text", "order_id", "created_at", "party_kind")}
                                           for r in rows]})


async def _last_status() -> dict:
    """Последняя смена статуса любого заказа: по ней портал замечает, что клиент отменил заказ или исполнитель начал работу."""
    h = await db.fetch_one("SELECT id, order_id, to_status, actor FROM order_statuses_log ORDER BY id DESC LIMIT 1")
    if not h:
        return {"last_status": 0, "status_order": 0, "status_text": ""}
    label = texts.STATUS_LABELS.get(h["to_status"], h["to_status"])
    if h["to_status"] == "cancelled":
        label = ("Отменён клиентом" if h["actor"].startswith("client")
                 else "Отменён администратором" if h["actor"].startswith(("admin", "portal")) else label)
    return {"last_status": h["id"], "status_order": h["order_id"], "status_text": f"Заказ №{h['order_id']}: {label}"}


@admin_page
async def api_summary(request: web.Request, uid: int) -> web.Response:
    one = lambda sql: db.fetch_one(sql)  # noqa: E731
    return web.json_response({
        "new_orders": (await one("SELECT COUNT(*) AS n FROM orders WHERE status='created'"))["n"],
        "new_requests": (await one("SELECT COUNT(*) AS n FROM web_requests WHERE status='new'"))["n"],
        "last_order": (await one("SELECT COALESCE(MAX(id),0) AS n FROM orders"))["n"],
        "last_request": (await one("SELECT COALESCE(MAX(id),0) AS n FROM web_requests"))["n"],
        "last_in": (await one("SELECT COALESCE(MAX(id),0) AS n FROM messages WHERE direction='in'"))["n"],
        **await _last_status(),
    })


def _pk_site(request: web.Request) -> tuple[set[str], str]:
    """Адрес сайта (origin) и домен (rpId), для которых выдаются и проверяются ключи: из WEBAPP_URL, а без него — из запроса."""
    from urllib.parse import urlparse
    if config.webapp_url:
        u = urlparse(config.webapp_url)
        origin = f"{u.scheme}://{u.netloc}"
    else:
        origin = f"{request.headers.get('X-Forwarded-Proto', request.scheme)}://{request.host}"
    return {origin}, urlparse(origin).hostname or ""


async def _pk_body(request: web.Request) -> dict | None:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return None
    return body if isinstance(body, dict) else None


@admin_page
async def api_passkey_options(request: web.Request, uid: int) -> web.Response:
    """Параметры для создания ключа Face ID на этом устройстве (только в выполненном входе)."""
    _, rp_id = _pk_site(request)
    exclude = [{"type": "public-key", "id": k["cred_id"]} for k in await passkeys.list_for(uid)]
    return web.json_response({"publicKey": {
        "challenge": passkeys.new_challenge("reg", uid),
        "rp": {"id": rp_id, "name": brand.NAME},
        "user": {"id": passkeys.b64u(uid.to_bytes(8, "big")), "name": f"admin-{uid}", "displayName": "Администратор"},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        "authenticatorSelection": {"authenticatorAttachment": "platform", "userVerification": "required",
                                   "residentKey": "required", "requireResidentKey": True},
        "attestation": "none", "timeout": 60000, "excludeCredentials": exclude}})


@admin_page
async def api_passkey_register(request: web.Request, uid: int) -> web.Response:
    body = await _pk_body(request)
    resp = body.get("response") if body else None
    bad = web.json_response({"error": "passkey"}, status=400)
    if not isinstance(resp, dict):
        return bad
    origins, rp_id = _pk_site(request)
    try:
        cred_id, point = passkeys.verify_registration(
            passkeys.unb64u(resp.get("clientDataJSON", "")), passkeys.unb64u(resp.get("attestationObject", "")),
            uid=uid, origins=origins, rp_id=rp_id)
        if str(body.get("id", "")) != passkeys.b64u(cred_id):
            raise passkeys.PasskeyError("id не совпал")
    except passkeys.PasskeyError as e:
        log.info("Face ID: ключ не принят: %s", e)
        return bad
    device = webchat.device_label(request.headers.get("User-Agent"))
    if not await passkeys.add(uid, cred_id, point, device):
        return bad
    return web.json_response({"ok": True})


async def passkey_options(request: web.Request) -> web.Response:
    """Строка для входа по ключу. Без входа: ключ найдёт сам телефон (ключ доступа привязан к сайту)."""
    if not _same_origin(request) or _limit(_pk_hits, _ip(request), 30, 600):
        return web.json_response({"error": "limit"}, status=429)
    _, rp_id = _pk_site(request)
    return web.json_response({"publicKey": {"challenge": passkeys.new_challenge("login"), "rpId": rp_id,
                                            "userVerification": "required", "timeout": 60000}})


async def passkey_err(request: web.Request) -> web.Response:
    """Телефон сообщает, почему Face ID не сработал (только имя ошибки), — чтобы причину видно было в журнале Railway."""
    if not _same_origin(request) or request.headers.get("X-Zc") != "1" or _limit(_pk_hits, _ip(request), 30, 600):
        return web.json_response({"error": "limit"}, status=429)
    body = await _pk_body(request) or {}
    where = re.sub(r"[^a-z]", "", str(body.get("where", "")).lower())[:12]
    name = re.sub(r"[^A-Za-z]", "", str(body.get("name", "")))[:40]
    log.info("Face ID на устройстве (%s): ошибка %s; %s", where, name, webchat.device_label(request.headers.get("User-Agent")))
    return web.json_response({"ok": True})


async def passkey_login(request: web.Request) -> web.Response:
    if not _same_origin(request):
        return web.json_response({"error": "origin"}, status=403)
    if _limit(_fails, _ip(request), FAIL_LIMIT, 3600):
        return web.json_response({"error": "limit"}, status=429)
    bad = web.json_response({"error": "passkey"}, status=400)
    body = await _pk_body(request)
    resp = body.get("response") if body else None
    if not isinstance(resp, dict):
        return bad
    origins, rp_id = _pk_site(request)
    try:
        row = await db.fetch_one("SELECT * FROM passkeys WHERE cred_id=?", (str(body.get("id", ""))[:200],))
        if not row or row["uid"] not in config.admin_ids:
            raise passkeys.PasskeyError("ключ неизвестен или не администратора")
        count = passkeys.verify_assertion(
            passkeys.unb64u(resp.get("clientDataJSON", "")), passkeys.unb64u(resp.get("authenticatorData", "")),
            passkeys.unb64u(resp.get("signature", "")), public_key=passkeys.unb64u(row["public_key"]),
            stored_count=row["sign_count"], origins=origins, rp_id=rp_id)
    except passkeys.PasskeyError as e:
        log.info("Face ID: вход не принят: %s", e)
        return bad
    await db.execute("UPDATE passkeys SET sign_count=?, last_used=datetime('now') WHERE id=?", (count, row["id"]))
    _fails.pop(_ip(request), None)
    token = await webchat.new_session(row["uid"], device=webchat.device_label(request.headers.get("User-Agent")))
    out = web.json_response({"ok": True})
    webchat._set_cookie(request, out, token, admin=True)
    return out


@admin_page
async def api_push_test(request: web.Request, uid: int) -> web.Response:
    """Проверочное уведомление на все устройства администратора; в ответе — сколько дошло."""
    import webpush
    return web.json_response(await webpush.notify(uid, "🔔 Проверка: уведомления работают."))


# ---------- Настройки входа: почта ----------

def _mail_card(email: str | None) -> str:
    """Что нужно для писем с кодом: чего не хватает в Railway, где взять пароль приложения, кнопка проверки."""
    import os
    miss = mailer.missing()
    if not miss:
        test = ('<form method="post" action="/portal/settings" style="margin-top:8px"><input type="hidden" name="step" value="mailtest">'
                '<button class="btn">Проверить почту</button></form>') if email else \
               '<p class="muted">Добавьте почту для входа выше — и здесь появится проверка.</p>'
        return (f'<div class="card" style="padding:14px;margin-top:10px"><b>Отправка писем с кодом</b>'
                f'<p>Настроена ✅ Письма уходят с адреса из SMTP_USER.</p>{test}</div>')
    pid, sid = os.getenv("RAILWAY_PROJECT_ID"), os.getenv("RAILWAY_SERVICE_ID")
    rw = (f'<a href="https://railway.com/project/{_e(pid)}/service/{_e(sid)}/variables" target="_blank" rel="noopener">'
          f'Railway → Variables</a>') if pid and sid else "Railway → ваш сервис → Variables"
    helper = mailer.password_help()
    how = (f'<li>Создайте пароль приложения: <a href="{_e(helper[1])}" target="_blank" rel="noopener">страница {_e(helper[0])}</a> '
           f'(нужна двухэтапная проверка в аккаунте). Название любое, например «Зооконсьерж».</li>') if helper else \
          '<li>В настройках вашей почты создайте «пароль приложения» (не обычный пароль от ящика).</li>'
    return (f'<div class="card" style="padding:14px;margin-top:10px"><b>Отправка писем с кодом</b>'
            f'<p>Пока не настроена. Не хватает в {rw}: <b>{_e(", ".join(miss))}</b>.</p>'
            f'<ol style="padding-left:20px;margin:0">{how}'
            f'<li>Откройте {rw}, нажмите «New Variable», имя <b>SMTP_PASSWORD</b>, в значение вставьте этот пароль и сохраните.</li>'
            f'<li>Подождите минуту, пока сервис перезапустится, и вернитесь сюда: появится кнопка «Проверить почту».</li></ol>'
            f'<p class="muted">Пароль вставляйте только в Railway: в чат и на сайт его не отправляйте.</p></div>')


@admin_page
async def settings_page(request: web.Request, uid: int) -> web.Response:
    email = await admin_email(uid)
    pending = await db.fetch_one("SELECT value FROM kv WHERE key=?", (f"admin_email_pending:{uid}",))
    mail_state = "Почта для писем настроена ✅" if mailer.configured() else "Почта для писем ещё не настроена (шаги ниже)."
    confirm = ""
    if pending:
        confirm = (f'<form method="post" action="/portal/settings" class="card" style="padding:14px;margin-top:10px">'
                   f'<input type="hidden" name="step" value="confirm"><label for="code">Код из письма на {_e(pending["value"])}</label>'
                   f'<input id="code" name="code" inputmode="numeric" maxlength="6" required><button class="btn" style="margin-left:6px">Подтвердить</button></form>')
    mine = request.cookies.get(webchat.ADMIN_COOKIE)
    me_hash = webchat._hash(mine) if mine else None
    devs = await db.fetch_all("SELECT id, token_hash, device, created_at FROM web_sessions WHERE uid=? ORDER BY id DESC", (uid,))
    rows = ""
    for d in devs:
        here = d["token_hash"] == me_hash
        rows += (f'<form method="post" action="/portal/settings" class="dev" style="display:flex;gap:8px;align-items:center;'
                 f'justify-content:space-between;padding:8px 0;border-top:1px solid var(--line)">'
                 f'<span>{_e(d["device"] or "Неизвестное устройство")}{" — это устройство" if here else ""}'
                 f'<br><small class="muted">вход {_e(d["created_at"])} (UTC)</small></span>'
                 f'<input type="hidden" name="step" value="revoke"><input type="hidden" name="id" value="{d["id"]}">'
                 f'<button class="btn" name="revoke" value="1">{"Выйти" if here else "Отозвать"}</button></form>')
    others = ""
    if len(devs) > 1:
        others = ('<form method="post" action="/portal/settings" style="margin-top:8px"><input type="hidden" name="step" value="revoke_others">'
                  '<button class="btn">Отозвать все остальные</button></form>')
    keys = await passkeys.list_for(uid)
    key_rows = "".join(
        f'<form method="post" action="/portal/settings" style="display:flex;gap:8px;align-items:center;justify-content:space-between;'
        f'padding:8px 0;border-top:1px solid var(--line)"><span>{_e(k["device"] or "Устройство")}'
        f'<br><small class="muted">создан {_e(k["created_at"])} (UTC){", вход " + _e(k["last_used"]) if k["last_used"] else ""}</small></span>'
        f'<input type="hidden" name="step" value="pk_revoke"><input type="hidden" name="id" value="{k["id"]}">'
        f'<button class="btn">Отозвать</button></form>' for k in keys)
    passkey_card = (f'<div class="card" style="padding:14px;margin-top:10px"><b>Вход по Face ID / Touch ID</b>'
                    f'<p class="muted">Вместо кода из Telegram: телефон узнаёт вас по лицу или отпечатку. Создавайте ключ на том устройстве, '
                    f'где пользуетесь порталом (на iPhone — открыв «ЗК Админ» с экрана «Домой»). Первый вход всегда по коду, '
                    f'ключ можно отозвать здесь в любой момент.</p>'
                    f'<button class="btn" id="pkon" type="button">Включить вход по Face ID</button>'
                    f'<p id="pkst" class="muted" style="margin-top:8px"></p>{key_rows}</div>{PASSKEY_JS}')
    devices = (f'<div class="card" style="padding:14px;margin-top:10px"><b>Доверенные устройства</b>'
               f'<p class="muted">Устройства, на которых вы вошли по коду. Повторный код с них не нужен, пока вход не отозван.</p>'
               f'{rows or "<p class=muted>Пока нет.</p>"}{others}</div>')
    n_push = (await db.fetch_one("SELECT COUNT(*) AS n FROM push_subs WHERE uid=?", (uid,)))["n"]
    push_card = (f'<div class="card" style="padding:14px;margin-top:10px"><b>Уведомления</b>'
                 f'<p>Устройств с уведомлениями: {n_push}</p>'
                 f'<p class="muted">Включайте в том приложении, которым пользуетесь: на iPhone — открыв «ЗК Админ» с экрана «Домой». '
                 f'Каждое устройство включается отдельно.</p>'
                 f'<button class="btn" id="pushon" type="button">Включить на этом устройстве</button> '
                 f'<button class="btn" id="pushtest" type="button">Проверить уведомления</button>'
                 f'<p id="pushst" class="muted" style="margin-top:8px"></p></div>')
    mail_card = _mail_card(email)
    body = (f'<h1>Вход и почта</h1><div class="card pre">Вы вошли как администратор (Telegram ID {uid}).\n'
            f'Почта для входа: {_e(email) if email else "не добавлена"}\n{_e(mail_state)}</div>'
            f'<form method="post" action="/portal/settings" class="card" style="padding:14px;margin-top:10px">'
            f'<input type="hidden" name="step" value="set"><label for="email">Почта для входа без Telegram</label>'
            f'<input id="email" name="email" type="email" required value="{_e(email or "")}" style="width:100%">'
            f'<button class="btn" style="margin-top:10px">Прислать код для проверки</button></form>{confirm}'
            f'<p class="muted">После проверки почты входите в портал с любого устройства без VPN: «Прислать код на почту».</p>'
            + mail_card + push_card + passkey_card + devices)
    return _html_resp(page("Вход и почта", body, "settings", request.query.get("m")))


@admin_page
async def settings_action(request: web.Request, uid: int) -> web.Response:
    form = await request.post()
    if form.get("step") == "set":
        email = str(form.get("email", "")).strip().lower()[:200]
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            return _redirect("/portal/settings", "Проверьте адрес почты.")
        if not mailer.configured():
            return _redirect("/portal/settings", "Сначала настройте отправку писем (см. выше).")
        await db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (f"admin_email_pending:{uid}", email))
        code = await _issue_code(uid, "email_reg")
        sent = await mailer.send(email, f"{brand.NAME}: подтверждение почты", f"Код подтверждения почты: {code}\nДействует 10 минут.")
        return _redirect("/portal/settings", "Код отправлен на почту." if sent else "Письмо не ушло: проверьте SMTP в Railway.")
    if form.get("step") == "mailtest":
        email = await admin_email(uid)
        if not email:
            return _redirect("/portal/settings", "Сначала добавьте почту для входа.")
        ok, why = await mailer.send_checked(email, f"{brand.NAME}: проверка почты",
                                            "Если вы видите это письмо, отправка писем с кодом входа настроена.")
        return _redirect("/portal/settings", f"Письмо отправлено на {email} ✅ Проверьте входящие и «Спам»." if ok
                         else f"Письмо не ушло: {why}.")
    if form.get("step") == "pk_revoke":
        rid = re.sub(r"\D", "", str(form.get("id", "")))
        if rid:
            await passkeys.revoke(uid, int(rid))
        return _redirect("/portal/settings", "Ключ Face ID отозван.")
    if form.get("step") in ("revoke", "revoke_others"):
        mine = request.cookies.get(webchat.ADMIN_COOKIE)
        if form.get("step") == "revoke":
            rid = re.sub(r"\D", "", str(form.get("id", "")))
            if rid:
                await db.execute("DELETE FROM web_sessions WHERE id=? AND uid=?", (int(rid), uid))
        else:
            await db.execute("DELETE FROM web_sessions WHERE uid=? AND token_hash<>?", (uid, webchat._hash(mine) if mine else ""))
        webchat._linked_cache.clear()
        webchat._targets_cache.clear()
        return _redirect("/portal/settings", "Устройство отозвано." if form.get("step") == "revoke" else "Остальные устройства отозваны.")
    if form.get("step") == "confirm":
        code = re.sub(r"\D", "", str(form.get("code", "")))
        row = await _check_code(code, ("email_reg",)) if len(code) == 6 else None
        pending = await db.fetch_one("SELECT value FROM kv WHERE key=?", (f"admin_email_pending:{uid}",))
        if not row or row["tg_id"] != uid or not pending:
            return _redirect("/portal/settings", "Код не подошёл.")
        await db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (f"admin_email:{uid}", pending["value"]))
        await db.execute("DELETE FROM kv WHERE key=?", (f"admin_email_pending:{uid}",))
        return _redirect("/portal/settings", "Почта подтверждена ✅ Теперь можно входить по коду на почту.")
    return _redirect("/portal/settings")


@admin_page
async def as_client(request: web.Request, uid: int) -> web.Response:
    """Режим проверки: администратор получает обычную клиентскую сессию (гостя) и видит сайт глазами клиента.
    Админская cookie остаётся, клиентскую можно сбросить кнопкой выхода в чате."""
    token = await webchat.new_session(None, request.headers.get("User-Agent", "")[:200], webchat.client_ip(request))
    resp = web.Response(status=302, headers={"Location": "/"})
    webchat._set_cookie(request, resp, token)
    return resp


@admin_page
async def reports_page(request: web.Request, uid: int) -> web.Response:
    """Отчёты: сводка по статусам и кнопка «вся база в Excel» (клиенты, заказы, заявки с сайта, история статусов)."""
    import exports
    rows = await exports.status_summary()
    table = "".join(
        f'<tr class="{STATUS_CLASS[r["status"]]}"><td>{_e(r["label"])}</td><td><b>{r["n"]}</b></td><td>{rub(r["total"])}</td>'
        f'<td>{r["today"]}</td><td>{r["week"]}</td></tr>' for r in rows)
    clients = (await db.fetch_one("SELECT COUNT(*) AS n FROM users WHERE pdn_consent=1 OR id IN (SELECT user_id FROM orders)"))["n"]
    body = (f'<h1>Отчёты и база</h1><div class="card" style="padding:14px"><b>База клиентов в Excel</b>'
            f'<p class="muted">Клиенты ({clients}), заказы, заявки с сайта, история статусов и сводка. Файл собирается заново при каждом нажатии.</p>'
            f'<a class="btn" href="/portal/export/database.xlsx">📥 Скачать базу (Excel)</a></div>'
            f'<h2>Заказы по статусам</h2><div class="card" style="padding:6px 10px;overflow-x:auto"><table class="stt-tab">'
            f'<tr><th>Статус</th><th>Сейчас</th><th>Сумма</th><th>Сегодня</th><th>7 дней</th></tr>{table}</table></div>'
            f'<style>.stt-tab{{width:100%;border-collapse:collapse}}.stt-tab th,.stt-tab td{{padding:8px 6px;text-align:left;border-bottom:1px solid var(--line)}}'
            f'.stt-tab th{{font-size:13px;color:var(--mute)}}</style>')
    return _html_resp(page("Отчёты и база", body, "reports"))


@admin_page
async def export_database(request: web.Request, uid: int) -> web.Response:
    import exports
    stamp = to_local(now_utc()).strftime("%Y-%m-%d_%H%M")
    return web.Response(body=await exports.build_database_xlsx(), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": f'attachment; filename="baza_klientov_{stamp}.xlsx"', "Cache-Control": "no-store"})


def setup(app: web.Application) -> None:
    app.router.add_get("/portal/reports", reports_page)
    app.router.add_get("/portal/export/database.xlsx", export_database)
    app.router.add_post("/portal/as-client", as_client)
    app.router.add_get("/portal", list_page)
    app.router.add_post("/portal/code", post_code)
    app.router.add_post("/portal/verify", post_verify)
    app.router.add_get("/portal/face", face_page)
    app.router.add_get("/portal/logout", logout)
    app.router.add_get("/portal/order/{oid}", order_page)
    app.router.add_post("/portal/order/{oid}", order_action)
    app.router.add_get("/portal/request/{rid}", request_page)
    app.router.add_post("/portal/request/{rid}", request_action)
    app.router.add_get("/portal/clients", clients_page)
    app.router.add_get("/portal/executors", executors_page)
    app.router.add_get("/portal/chat/{tg}", chat_page)
    app.router.add_get("/portal/api/chat/{tg}", api_chat)
    app.router.add_post("/portal/api/chat/{tg}", api_chat)
    app.router.add_get("/portal/api/summary", api_summary)
    app.router.add_post("/portal/api/push/test", api_push_test)
    app.router.add_post("/portal/api/passkey/options", api_passkey_options)
    app.router.add_post("/portal/api/passkey/register", api_passkey_register)
    app.router.add_post("/portal/passkey/options", passkey_options)
    app.router.add_post("/portal/passkey/login", passkey_login)
    app.router.add_post("/portal/passkey/err", passkey_err)
    import tariffs_web
    tariffs_web.setup(app)
    import analytics
    analytics.setup_portal(app)
    app.router.add_get("/portal/settings", settings_page)
    app.router.add_post("/portal/settings", settings_action)


__all__ = ["setup", "json", "quote"]
