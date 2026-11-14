"""Вход клиента по Face ID / Touch ID (ключ доступа, WebAuthn) — для повторных входов без кодов.

Ключ создаётся только в уже выполненном входе (Telegram, Яндекс ID, код из письма или Telegram): в чате появляется
плашка «Входить по Face ID?». Потом на странице входа кнопка «Войти по Face ID» (только на том устройстве, где ключ
включён). Проверка подписи — та же, что у администратора (passkeys.py). Ключи клиента и администратора лежат в одной
таблице, но не пересекаются: ключ администратора кабинет клиента не открывает, ключ клиента не открывает портал
(portal.passkey_login пускает только ADMIN_IDS)."""
import logging

from aiohttp import web

import db
import passkeys
import portal
import webchat
from config import config

log = logging.getLogger(__name__)

_hits: dict = {}
_fails: dict = {}
FAIL_LIMIT = 10


def _ok_request(request: web.Request) -> bool:
    return portal._same_origin(request) and request.headers.get("X-Zc") == "1"


def _uid_bytes(uid: int) -> str:
    return passkeys.b64u(uid.to_bytes(8, "big", signed=True))   # у гостей с сайта id отрицательные


async def reg_options(request: web.Request) -> web.Response:
    """Параметры для создания ключа на этом устройстве — только для клиента, который уже вошёл."""
    if not _ok_request(request) or portal._limit(_hits, portal._ip(request), 30, 600):
        return web.json_response({"error": "limit"}, status=429)
    uid = await webchat.session_uid(request)
    if uid is None or not await webchat._logged_in(request):
        return web.json_response({"error": "login"}, status=403)
    _, rp_id = portal._pk_site(request)
    row = await db.fetch_one("SELECT name FROM users WHERE tg_id=?", (uid,))
    name = (row["name"] if row and row["name"] else "Клиент Зооконсьержа")[:60]
    exclude = [{"type": "public-key", "id": k["cred_id"]} for k in await passkeys.list_for(uid)]
    return web.json_response({"publicKey": {
        "challenge": passkeys.new_challenge("reg", uid),
        "rp": {"id": rp_id, "name": "Зооконсьерж"},
        "user": {"id": _uid_bytes(uid), "name": f"client-{uid}", "displayName": name},
        "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
        "authenticatorSelection": {"authenticatorAttachment": "platform", "userVerification": "required",
                                   "residentKey": "required", "requireResidentKey": True},
        "attestation": "none", "timeout": 60000, "excludeCredentials": exclude}})


async def register(request: web.Request) -> web.Response:
    bad = web.json_response({"error": "passkey"}, status=400)
    if not _ok_request(request):
        return web.json_response({"error": "origin"}, status=403)
    uid = await webchat.session_uid(request)
    if uid is None or not await webchat._logged_in(request):
        return web.json_response({"error": "login"}, status=403)
    body = await portal._pk_body(request)
    resp = body.get("response") if body else None
    if not isinstance(resp, dict):
        return bad
    origins, rp_id = portal._pk_site(request)
    try:
        cred_id, point = passkeys.verify_registration(
            passkeys.unb64u(resp.get("clientDataJSON", "")), passkeys.unb64u(resp.get("attestationObject", "")),
            uid=uid, origins=origins, rp_id=rp_id)
        if str(body.get("id", "")) != passkeys.b64u(cred_id):
            raise passkeys.PasskeyError("id не совпал")
    except passkeys.PasskeyError as e:
        log.info("Face ID клиента: ключ не принят: %s", e)
        return bad
    if not await passkeys.add(uid, cred_id, point, webchat.device_label(request.headers.get("User-Agent"))):
        return bad
    return web.json_response({"ok": True})


async def login_options(request: web.Request) -> web.Response:
    if not _ok_request(request) or portal._limit(_hits, portal._ip(request), 30, 600):
        return web.json_response({"error": "limit"}, status=429)
    _, rp_id = portal._pk_site(request)
    return web.json_response({"publicKey": {"challenge": passkeys.new_challenge("login"), "rpId": rp_id,
                                            "userVerification": "required", "timeout": 60000}})


async def login(request: web.Request) -> web.Response:
    if not _ok_request(request):
        return web.json_response({"error": "origin"}, status=403)
    if portal._limit(_fails, portal._ip(request), FAIL_LIMIT, 3600):
        return web.json_response({"error": "limit"}, status=429)
    bad = web.json_response({"error": "passkey"}, status=400)
    body = await portal._pk_body(request)
    resp = body.get("response") if body else None
    if not isinstance(resp, dict):
        return bad
    row = await db.fetch_one("SELECT * FROM passkeys WHERE cred_id=?", (str(body.get("id", ""))[:200],))
    if not row:
        return bad
    if row["uid"] in config.admin_ids:                  # ключ администратора кабинет клиента не открывает
        return web.json_response({"error": "admin", "text": webchat.ADMIN_AS_CLIENT_TEXT}, status=403)
    origins, rp_id = portal._pk_site(request)
    try:
        count = passkeys.verify_assertion(
            passkeys.unb64u(resp.get("clientDataJSON", "")), passkeys.unb64u(resp.get("authenticatorData", "")),
            passkeys.unb64u(resp.get("signature", "")), public_key=passkeys.unb64u(row["public_key"]),
            stored_count=row["sign_count"], origins=origins, rp_id=rp_id)
    except passkeys.PasskeyError as e:
        log.info("Face ID клиента: вход не принят: %s", e)
        return bad
    await db.execute("UPDATE passkeys SET sign_count=?, last_used=datetime('now') WHERE id=?", (count, row["id"]))
    _fails.pop(portal._ip(request), None)
    token = await webchat.new_session(row["uid"], device=webchat.device_label(request.headers.get("User-Agent")),
                                      ip=webchat.client_ip(request))
    out = web.json_response({"ok": True})
    webchat._set_cookie(request, out, token)
    return out


# Тексты отказов для клиента (у администратора свои — про портал).
_WHY = r"""
function why(err){var n=(err&&err.name)||'';
  if(n==='NotAllowedError')return 'Face ID не подтверждён или отменён. Нажмите ещё раз и посмотрите на телефон.';
  if(n==='InvalidStateError')return 'На этом телефоне Face ID для сайта уже включён.';
  if(n==='NotSupportedError')return 'На этом устройстве нет Face ID для сайтов.';
  return 'Не получилось. Войдите через Telegram или по коду.'}
"""

# Страница входа: кнопка «Войти по Face ID» видна только там, где клиент включал Face ID (иначе только путает).
LOGIN_JS = ("<script>(function(){" + portal._PK_JS_COMMON + _WHY + r"""
var b=document.getElementById('pklogin');
if(!b||!window.PublicKeyCredential||!navigator.credentials)return;
var has=false;try{has=!!localStorage.getItem('zc_cpk')}catch(x){}
if(!has)return;
b.hidden=false;
var box=prep('/chat/login/passkey/options');
b.onclick=function(ev){
  ev.preventDefault();
  var pk=box.take();
  if(!pk){say('Готовлю вход… нажмите ещё раз через секунду.');box.load();return}
  say('Подтвердите Face ID…');
  navigator.credentials.get({publicKey:pk}).then(function(c){
    var r=c.response;
    return fetch('/chat/login/passkey',{method:'POST',headers:H,credentials:'same-origin',body:JSON.stringify({id:e(c.rawId),
      response:{clientDataJSON:e(r.clientDataJSON),authenticatorData:e(r.authenticatorData),signature:e(r.signature)}})});
  }).then(function(r){
    if(r.ok){location.replace('/chat');return}
    box.load();
    if(r.status===403){r.json().then(function(j){say(j.text||'Этот ключ не подходит.')}).catch(function(){});return}
    say(r.status===429?'Слишком много попыток. Войдите через Telegram.':'Ключ не подошёл. Войдите через Telegram и включите Face ID заново.');
  }).catch(function(err){box.load();report('clogin',err);say(why(err))});
};
})();</script>""")

# Плашка в чате для вошедшего клиента: «Входить по Face ID?» Показывается, где телефон умеет Face ID для сайтов,
# ключа на этом устройстве ещё нет и клиент не нажал «Не сейчас».
OFFER_HTML = (
    "<div id=\"fid\" style=\"display:none;margin:8px 10px 0;padding:8px 8px 8px 12px;border-radius:14px;background:#fff;"
    "border:1.5px solid var(--line);align-items:center;gap:8px;font-size:14px\">"
    "<span id=\"pkst\" style=\"flex:1;line-height:1.3\">Входить на сайт по Face ID, без кодов?</span>"
    "<button id=\"pkon\" type=\"button\" style=\"font:700 14px var(--font);height:36px;padding:0 14px;border:0;"
    "border-radius:10px;background:var(--coral);color:#fff\">Включить</button>"
    "<button id=\"fidx\" type=\"button\" aria-label=\"Не сейчас\" style=\"font:400 18px var(--font);width:32px;height:36px;"
    "border:0;background:none;color:var(--mute)\">✕</button></div>"
    "<script>(function(){" + portal._PK_JS_COMMON + _WHY + r"""
var w=document.getElementById('fid'),b=document.getElementById('pkon'),x=document.getElementById('fidx');
function ls(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(e){return null}}
if(!w||!window.PublicKeyCredential||!navigator.credentials||ls('zc_cpk')||ls('zc_cpk_no'))return;
var chk=PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable;
(chk?chk.call(PublicKeyCredential):Promise.resolve(true)).then(function(ok){if(ok)w.style.display='flex'}).catch(function(){});
x.onclick=function(){ls('zc_cpk_no','1');w.style.display='none'};
var box=prep('/chat/api/passkey/options',function(pk){pk.user.id=d(pk.user.id);(pk.excludeCredentials||[]).forEach(function(c){c.id=d(c.id)})});
b.onclick=function(ev){
  ev.preventDefault();
  var pk=box.take();
  if(!pk){say('Секунду… нажмите ещё раз.');box.load();return}
  say('Подтвердите Face ID…');
  navigator.credentials.create({publicKey:pk}).then(function(c){
    var r=c.response;
    return fetch('/chat/api/passkey/register',{method:'POST',headers:H,credentials:'same-origin',body:JSON.stringify({id:e(c.rawId),
      response:{clientDataJSON:e(r.clientDataJSON),attestationObject:e(r.attestationObject)}})});
  }).then(function(r){
    if(!r.ok){box.load();say('Не получилось. Нажмите «Включить» ещё раз.');return}
    ls('zc_cpk','1');say('Face ID включён ✅ В следующий раз войдёте одним взглядом.');b.style.display='none';
    setTimeout(function(){w.style.display='none'},4000);
  }).catch(function(err){box.load();report('cregister',err);
    if(err&&err.name==='InvalidStateError'){ls('zc_cpk','1');w.style.display='none';return}say(why(err))});
};
})();</script>""")


def setup(app: web.Application) -> None:
    app.router.add_post("/chat/api/passkey/options", reg_options)
    app.router.add_post("/chat/api/passkey/register", register)
    app.router.add_post("/chat/login/passkey/options", login_options)
    app.router.add_post("/chat/login/passkey", login)
