"""Чат сайта: панель «Найти на карте / Отправить геопозицию» не залипает, когда шаг уже пройден.
Раньше её гасил только ответ текстом; нажатие кнопки под сообщением и перезагрузка страницы оставляли её висеть."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_chat_panel.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
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


PANEL = [[{"t": "🗺 Найти на карте", "map": "/map"}, {"t": "📍 Отправить геопозицию", "loc": True}]]


async def panel_visible(page):
    return await page.evaluate("getComputedStyle(document.getElementById('rk')).display !== 'none' && document.querySelectorAll('#rk button').length > 0")


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
        ctx = await br.new_context(viewport={"width": 390, "height": 780})
        page = await ctx.new_page()
        await page.goto(base + "/chat")
        await page.wait_for_timeout(1200)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]

        # 1. панель шага адреса видна, пока шаг идёт
        await webchat.push(uid, "send", who="bot", text="Адрес, куда ехать?", reply=PANEL, once=True)
        await page.wait_for_timeout(1500)
        check(await panel_visible(page), "на шаге адреса панель видна")
        await page.reload()
        await page.wait_for_timeout(1500)
        check(await panel_visible(page), "после перезагрузки на том же шаге панель видна")

        # 2. человек нажал кнопку под сообщением и ушёл с шага: панель гаснет сразу и не возвращается после перезагрузки
        await webchat.push(uid, "send", who="bot", text="Или оформим заявку?", inline=[[{"t": "Отмена", "cb": "zz:cancel"}]])
        await page.wait_for_timeout(1500)
        await page.click("text=Отмена")
        await page.wait_for_timeout(1500)
        check(not await panel_visible(page), "после нажатия кнопки под сообщением панель убралась")
        await page.reload()
        await page.wait_for_timeout(1500)
        check(not await panel_visible(page), "после перезагрузки панель не вернулась")

        # 3. то же после ответа текстом
        await webchat.push(uid, "send", who="bot", text="Куда поедем?", reply=PANEL, once=True)
        await page.wait_for_timeout(1500)
        check(await panel_visible(page), "новый шаг: панель снова видна")
        await page.fill("#t", "Привет")
        await page.press("#t", "Enter")
        await page.wait_for_timeout(2000)
        check(not await panel_visible(page), "после ответа текстом панель убралась")
        await page.reload()
        await page.wait_for_timeout(1500)
        check(not await panel_visible(page), "после ответа текстом и перезагрузки панель не вернулась")
        await br.close()
    await runner.cleanup()
    await db.close_shared()
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
