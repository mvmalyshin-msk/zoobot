"""Адрес на сайте в настоящем Chromium: кнопки «Найти на карте» и «Отправить геопозицию» работают,
адрес с карты возвращается в чат. Запуск: DB_PATH=/tmp/chatmap.db python3 tests/e2e_chatmap.py (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_chatmap.db")
for sfx in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + sfx)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiohttp import web  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402
from bot import build_dispatcher  # noqa: E402

FAILS = []


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...
    async def make_request(self, bot, method, timeout=None):
        return True


async def events_text(uid):
    rows = await db.fetch_all("SELECT payload FROM web_events WHERE uid=? AND op='send'", (uid,))
    return " ".join(r["payload"] for r in rows)


# Настоящий telegram-web-app.js создаёт Telegram.WebApp в ЛЮБОМ браузере: вне Telegram platform="unknown",
# а sendData ничего не делает. Без этой подмены в песочнице скрипт не грузится и баг не виден.
TG_OUTSIDE = ("window.Telegram={WebApp:{platform:'unknown',initData:'',version:'6.0',"
              "ready:function(){},expand:function(){},sendData:function(){window.__sentToTelegram=true}}};")
TG_INSIDE = ("window.Telegram={WebApp:{platform:'ios',initData:'',version:'7.0',"
             "ready:function(){},expand:function(){},sendData:function(d){window.__sentToTelegram=d}}};")
TG_JS = "https://telegram.org/js/telegram-web-app.js"


async def _fake_tg(ctx, body):
    await ctx.route(TG_JS, lambda route: route.fulfill(status=200, content_type="application/javascript", body=body))


async def main():
    await db.init_db()
    bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
    app = webserver.make_app()
    app["bot"], app["dp"] = bot, build_dispatcher()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    async with async_playwright() as p:
        br = await p.chromium.launch()
        ctx = await br.new_context(viewport={"width": 390, "height": 780}, geolocation={"latitude": 55.9, "longitude": 37.4},
                                   permissions=["geolocation"])
        await _fake_tg(ctx, TG_OUTSIDE)
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/chat")
        await page.wait_for_timeout(1200)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
        await webchat.push(uid, "send", who="bot", text="Адрес, куда ехать?",
                           reply=[[{"t": "🗺 Найти на карте", "map": base + "/map"}, {"t": "📍 Отправить геопозицию", "loc": True}]])
        await page.wait_for_timeout(1500)
        buttons = await page.eval_on_selector_all("#rk button", "bs => bs.map(b => b.textContent)")
        check("🗺 Найти на карте" in buttons and "📍 Отправить геопозицию" in buttons, "кнопки карты и геопозиции видны в чате")

        await page.click("text=📍 Отправить геопозицию")
        await page.wait_for_timeout(2000)
        check("📍 Геопозиция отправлена" in await events_text(uid), "геопозиция из браузера дошла до бота")

        await page.click("text=🗺 Найти на карте")
        await page.wait_for_url(base + "/map", timeout=8000)
        rib = await page.evaluate("""() => { const r = document.querySelector('.role-ribbon'); if (!r) return null;
            const b = r.getBoundingClientRect(); const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
            return {h: b.height, top: b.top, visible: !!top && (top === r || r.contains(top))}; }""")
        check(bool(rib) and rib["visible"] and rib["h"] > 0 and rib["top"] >= 0, f"синяя лента на карте видна сверху: {rib}")
        over = await page.evaluate("() => document.documentElement.scrollHeight - window.innerHeight")
        check(over <= 1, f"карта помещается в экран, без прокрутки страницы (лишние {over}px)")
        await page.evaluate("() => window.__test.setPoint(55.9, 37.4, '', null)")
        await page.fill("#label", "Химки, Железнодорожная, 33")
        await page.click("#ok")
        await page.wait_for_url(base + "/chat", timeout=8000)
        await page.wait_for_timeout(1500)
        check("🗺 Адрес выбран на карте" in await events_text(uid), "адрес с карты вернулся в чат")
        check(not errors, "в консоли нет ошибок JS: " + "; ".join(errors))

        # геопозиция на компьютере: точный запрос не успевает (у Mac нет GPS) — чат повторяет с обычной точностью
        async def geo_page(script):
            gctx = await br.new_context(viewport={"width": 390, "height": 780})
            await _fake_tg(gctx, TG_OUTSIDE)
            await gctx.add_init_script(script)
            gp = await gctx.new_page()
            gp.on("pageerror", lambda e: errors.append(str(e)))
            await gp.goto(base + "/chat")
            await gp.wait_for_timeout(1200)
            guid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
            await webchat.push(guid, "send", who="bot", text="Адрес?",
                               reply=[[{"t": "🗺 Найти на карте", "map": base + "/map"}, {"t": "📍 Отправить геопозицию", "loc": True}]])
            await gp.wait_for_selector("text=📍 Отправить геопозицию", timeout=5000)
            await gp.click("text=📍 Отправить геопозицию")
            await gp.wait_for_timeout(1500)
            return gp, guid

        gp, guid = await geo_page("""navigator.geolocation.getCurrentPosition = function (ok, fail, o) {
            if (o && o.enableHighAccuracy) { setTimeout(function () { fail({code: 3, message: 'timeout'}); }, 50); return; }
            ok({coords: {latitude: 55.9516, longitude: 37.3036, accuracy: 900}}); };""")
        check("📍 Геопозиция отправлена" in await events_text(guid), "без GPS: повтор с обычной точностью дошёл до бота")
        gp, _ = await geo_page("""navigator.geolocation.getCurrentPosition = function (ok, fail) {
            setTimeout(function () { fail({code: 1, message: 'denied'}); }, 50); };""")
        st = await gp.inner_text("#st")
        check("разрешите" in st.lower() and "Найти на карте" in st, f"доступ запрещён: подсказка, как разрешить, и карта: {st}")
        gp, _ = await geo_page("""navigator.geolocation.getCurrentPosition = function (ok, fail) {
            setTimeout(function () { fail({code: 2, message: 'unavailable'}); }, 50); };""")
        st = await gp.inner_text("#st")
        check("Найти на карте" in st and "разрешите" not in st.lower(), f"место не определилось: предлагаем карту: {st}")

        # внутри Telegram (мини-приложение с кнопки) адрес по-прежнему уходит через sendData, а не на сайт
        ctx2 = await br.new_context(viewport={"width": 390, "height": 780})
        await _fake_tg(ctx2, TG_INSIDE)
        page2 = await ctx2.new_page()
        await page2.goto(base + "/map")
        await page2.evaluate("() => window.__test.setPoint(55.9, 37.4, '', null)")
        await page2.fill("#label", "Химки, Железнодорожная, 33")
        await page2.click("#ok")
        await page2.wait_for_timeout(800)
        sent = await page2.evaluate("() => window.__sentToTelegram || null")
        check(bool(sent) and "Железнодорожная" in str(sent), "в Telegram адрес уходит через sendData")
        check(page2.url == base + "/map", "в Telegram страница не уходит на сайт")
        await br.close()
    await runner.cleanup()
    print("\nИТОГ: " + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"НЕ ПРОЙДЕНО: {len(FAILS)}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
