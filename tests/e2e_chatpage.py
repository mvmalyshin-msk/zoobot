"""Страница /chat в настоящем Chromium: чат оживает, кнопки жмутся, админ входит по одноразовой ссылке.
Запуск: BOT_TOKEN=1:x ADMIN_IDS=111 DB_PATH=/tmp/chatpage.db python3 tests/e2e_chatpage.py (нужен Playwright)"""
import asyncio
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_chatpage.db")
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


async def main():
    await db.init_db()
    bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
    app = webserver.make_app()
    app["bot"], app["dp"] = bot, build_dispatcher()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    async with async_playwright() as p:
        br = await p.chromium.launch()
        errors = []
        page = await (await br.new_context(viewport={"width": 390, "height": 780})).new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept("+7 900 111-22-33")))
        await page.goto(base + "/chat")
        await page.wait_for_selector(".m.bot", timeout=8000)
        check("Зоо-консьерж" in await page.inner_text("header"), "заголовок есть")
        check("согласие" in (await page.inner_text("#log")).lower(), "бот показал экран согласия")
        await page.click(".kb button >> nth=0")  # «Согласен» (первая кнопка)
        await page.wait_for_function("document.querySelectorAll('.m.bot').length>1", timeout=8000)
        check(True, "после нажатия кнопки пришёл следующий шаг")
        await page.fill("#t", "<b>привет</b> https://example.com/x")
        await page.press("#t", "Enter")
        await page.wait_for_function("document.querySelectorAll('.m.me').length>1", timeout=8000)
        mine = await page.locator(".m.me .tx").last.inner_html()
        check("<b>" not in mine and "&lt;b&gt;" in mine, "текст экранируется, HTML не исполняется")
        check('href="https://example.com/x"' in mine, "ссылка кликабельна")
        # админ
        link = await webchat.make_login_link(111)
        tok = re.search(r"t=(.+)$", link).group(1)
        actx = await br.new_context(viewport={"width": 390, "height": 780})
        ap = await actx.new_page()
        ap.on("pageerror", lambda e: errors.append(str(e)))
        await ap.goto(f"{base}/chat/login?t={tok}")
        await ap.click("button")
        await ap.wait_for_selector(".m.bot", timeout=8000)
        check(await ap.is_visible("#ad"), "админ распознан")
        await webchat_notify(bot)
        await ap.wait_for_function("document.body.innerText.includes('Новая заявка из теста')", timeout=8000)
        check(True, "уведомление админу пришло в открытый чат без перезагрузки")
        await ap.reload()
        check("Новая заявка из теста" in await ap.inner_text("#log"), "после перезагрузки история на месте")
        check(not errors, "в консоли нет ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {FAILS}")
    sys.exit(1 if FAILS else 0)


async def webchat_notify(bot):
    import orders
    await orders.notify(bot, 111, "Новая заявка из теста")


asyncio.run(main())
