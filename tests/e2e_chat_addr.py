"""Чат сайта: на шаге адреса в строке ввода появляются подсказки адреса; на других шагах — нет.
Photon подменён.   Запуск:  python3 tests/e2e_chat_addr.py   (нужен Playwright)"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_chat_addr.db"
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
LABEL = "Ленина улица, 5, Тверской, Москва"
PHOTON = {"features": [{"geometry": {"coordinates": [37.6, 55.7]}, "properties": {
    "street": "Ленина улица", "housenumber": "5", "district": "Тверской", "city": "Москва", "state": "Москва"}}]}
PANEL = [[{"t": "🗺 Найти на карте", "map": "/map"}, {"t": "📍 Отправить геопозицию", "loc": True}]]


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...
    async def make_request(self, bot, method, timeout=None):
        return True


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
        await ctx.route("https://photon.komoot.io/**", lambda r: r.fulfill(
            status=200, content_type="application/json", headers={"access-control-allow-origin": "*"}, body=json.dumps(PHOTON)))
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/chat")
        await page.wait_for_timeout(1200)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]

        await webchat.push(uid, "send", who="bot", text="Какой адрес?", reply=PANEL, once=True)
        await page.wait_for_timeout(1500)
        await page.fill("#t", "ленина 5")
        await page.wait_for_selector("#sg .sug", timeout=5000)
        check(await page.locator("#sg .sug").count() == 1, "на шаге адреса под строкой ввода выпадает подсказка")
        check(await page.locator("#sg .sug").get_attribute("data-pick") == LABEL, "в подсказке полный адрес")
        await page.click("#sg .sug")
        await page.wait_for_function("[...document.querySelectorAll('#log .m.me')].some(m=>m.textContent.includes('Ленина улица, 5'))", timeout=5000)
        check(True, "выбранный адрес ушёл в чат")
        check(await page.locator("#sg .sug").count() == 0, "список закрылся")
        check(await page.input_value("#t") == "", "строка ввода очистилась")

        # шаг пройден (панель ушла): подсказок нет
        await page.wait_for_timeout(1500)
        await page.fill("#t", "ленина 5")
        await page.wait_for_timeout(1200)
        check(await page.locator("#sg .sug").count() == 0, "на других шагах подсказок нет")
        check(not errors, f"ошибок в консоли нет: {errors}")
        await br.close()
    await runner.cleanup()
    await db.close_shared()
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
