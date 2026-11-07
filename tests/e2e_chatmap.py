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
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/chat")
        await page.wait_for_timeout(1200)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
        await webchat.push(uid, "send", who="bot", text="Адрес, куда ехать?",
                           reply=[[{"t": "🗺 Найти на карте", "map": base + "/"}, {"t": "📍 Отправить геопозицию", "loc": True}]])
        await page.wait_for_timeout(1500)
        buttons = await page.eval_on_selector_all("#rk button", "bs => bs.map(b => b.textContent)")
        check("🗺 Найти на карте" in buttons and "📍 Отправить геопозицию" in buttons, "кнопки карты и геопозиции видны в чате")

        await page.click("text=📍 Отправить геопозицию")
        await page.wait_for_timeout(2000)
        check("📍 Геопозиция отправлена" in await events_text(uid), "геопозиция из браузера дошла до бота")

        await page.click("text=🗺 Найти на карте")
        await page.wait_for_url(base + "/", timeout=8000)
        await page.evaluate("() => window.__test.setPoint(55.9, 37.4, '', null)")
        await page.fill("#label", "Химки, Железнодорожная, 33")
        await page.click("#ok")
        await page.wait_for_url(base + "/chat", timeout=8000)
        await page.wait_for_timeout(1500)
        check("🗺 Адрес выбран на карте" in await events_text(uid), "адрес с карты вернулся в чат")
        check(not errors, "в консоли нет ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ: " + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"НЕ ПРОЙДЕНО: {len(FAILS)}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
