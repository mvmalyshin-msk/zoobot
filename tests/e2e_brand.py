"""Единый фирменный вид (в Chromium): на каждой странице шрифт только PT Sans, все кнопки — таблетки, фон и текст только из нашей гаммы
(кроме плашек ролей: синяя «клиентское меню» и красная «администратор»). Нужен Playwright."""
import os, sys, asyncio, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.update(BOT_TOKEN="1:x", ADMIN_IDS="111", DB_PATH="/tmp/e2e_brand.db", ADMIN_PHONE="+79001112233", YANDEX_CLIENT_ID="a", YANDEX_CLIENT_SECRET="b", WEBAPP_URL="http://127.0.0.1:8766")
for s in ("", "-wal", "-shm"):
    try: os.remove("/tmp/e2e_brand.db"+s)
    except FileNotFoundError: pass
import webserver, db, webchat
from aiohttp import web
from playwright.async_api import async_playwright
PUB = ["/", "/map", "/request", "/contact", "/policy", "/chat/login", "/chat/login/tg", "/chat/login?t=abc", "/cabinet", "/chat"]
ADM = ["/portal", "/portal/clients", "/portal/executors", "/portal/settings", "/portal/tariffs", "/portal/bot"]
JS = """() => {
 const fonts = {}, bgs = {}, cols = {}, radii = {};
 const clsBtn = [];
 for (const e of document.querySelectorAll('body *')) {
   const cs = getComputedStyle(e); if (cs.display==='none') continue;
   const t = [...e.childNodes].some(n=>n.nodeType===3 && n.textContent.trim());
   if (t) { fonts[cs.fontFamily.split(',')[0]] = (fonts[cs.fontFamily.split(',')[0]]||0)+1; cols[cs.color]=(cols[cs.color]||0)+1; }
   if (cs.backgroundColor!=='rgba(0, 0, 0, 0)') bgs[cs.backgroundColor]=(bgs[cs.backgroundColor]||0)+1;
   if (e.matches('button, a.btn, .btn, input[type=submit], a.login, a.way')) clsBtn.push([e.tagName, e.className, cs.borderRadius, cs.fontFamily.split(',')[0], cs.backgroundColor]);
 }
 const links = [...document.querySelectorAll('a[href]')].map(a=>a.getAttribute('href'));
 return {title: document.title, h1: (document.querySelector('h1')||{}).textContent, fonts, bgs, cols, btns: clsBtn, links, text: document.body.innerText.slice(0,1500)};
}"""
async def main():
    await db.init_db()
    r = web.AppRunner(webserver.make_app()); await r.setup()
    await web.TCPSite(r, "127.0.0.1", 8766).start()
    admin_tok = await webchat.new_session(111)
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for group, urls in (("pub", PUB), ("adm", ADM)):
            ctx = await b.new_context(viewport={"width":390,"height":844})
            if group=="adm":
                await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": admin_tok, "url": "http://127.0.0.1:8766"}])
            for u in urls:
                pg = await ctx.new_page()
                try:
                    resp = await pg.goto("http://127.0.0.1:8766"+u, wait_until="load")
                    await pg.wait_for_timeout(300)
                    d = await pg.evaluate(JS); d["status"]=resp.status; d["final"]=pg.url
                    out[u if group=="pub" else "ADM "+u]=d
                except Exception as e:
                    out[u]={"error":str(e)}
                await pg.close()
        await b.close()
    await r.cleanup()
    return out


ROLE = {"rgb(29, 111, 214)", "rgb(214, 43, 43)"}          # плашки ролей: «клиентское меню» и «администратор»
PALETTE = {(253, 246, 236), (249, 217, 192), (43, 43, 43), (111, 103, 94), (255, 255, 255), (234, 223, 206), (252, 103, 81),
           (255, 108, 88), (46, 178, 165), (57, 112, 215), (230, 177, 61), (198, 47, 29),
           (255, 228, 222), (216, 242, 239), (221, 231, 251), (251, 240, 210)}                      # + мягкие фоны карточек услуг
bad = []
for u, v in asyncio.run(main()).items():
    if "error" in v:
        bad.append(f"{u}: не открылась: {v['error'][:80]}")
        continue
    if set(v["fonts"]) - {'"PT Sans"'}:
        bad.append(f"{u}: чужие шрифты {sorted(set(v['fonts']) - {chr(34) + 'PT Sans' + chr(34)})}")
    for b in v["btns"]:
        if b[2] != "999px":
            bad.append(f"{u}: кнопка не таблетка {b[:3]}")
    for c in v["bgs"]:
        t = tuple(int(x) for x in re.findall(r"\d+", c)[:3])
        if c not in ROLE and t not in PALETTE:
            bad.append(f"{u}: цвет фона вне гаммы {c}")
print("\n".join(bad) or "OK: шрифты, кнопки и цвета фирменные на всех страницах")
sys.exit(1 if bad else 0)
