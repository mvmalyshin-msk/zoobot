"""Страница /chat в Chromium: после зависшего запроса (как Safari при сворачивании) чат снова получает сообщения.
Запуск: BOT_TOKEN=1:x ADMIN_IDS=111 DB_PATH=/tmp/chatresume.db python3 tests/e2e_chatresume.py (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_chatresume.db")
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
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    async with async_playwright() as p:
        br = await p.chromium.launch()
        page = await (await br.new_context(viewport={"width": 390, "height": 780})).new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        hung = {"n": 0}

        async def hang_first(route):
            # первый запрос опроса (тот, что уходит сразу после загрузки) не получает ответа — как в Safari после сворачивания
            hung["n"] += 1
            if hung["n"] == 1:
                await asyncio.Event().wait()
            else:
                await route.continue_()

        await page.route("**/chat/api/poll*", hang_first)
        await page.goto(base + "/chat")
        await page.wait_for_timeout(1500)
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]

        print("Зависший запрос опроса, как при сворачивании Safari:")
        await page.wait_for_timeout(300)
        await webchat.push(uid, "send", who="bot", text="пришло, пока Safari спал")
        await page.wait_for_timeout(1500)
        shown = await page.evaluate("()=>document.body.innerText.includes('пришло, пока Safari спал')")
        check(not shown, "до возврата сообщение ещё не видно (запрос завис)")

        # возврат в страницу: браузер присылает visibilitychange
        await page.evaluate("()=>document.dispatchEvent(new Event('visibilitychange'))")
        await page.wait_for_timeout(2500)
        shown = await page.evaluate("()=>document.body.innerText.includes('пришло, пока Safari спал')")
        check(shown, "после возврата в страницу сообщение пришло без перезагрузки")

        await webchat.push(uid, "send", who="bot", text="и следующее сообщение тоже")
        await page.wait_for_timeout(2000)
        shown = await page.evaluate("()=>document.body.innerText.includes('и следующее сообщение тоже')")
        check(shown, "опрос продолжается после восстановления")
        check(not errors, "в консоли нет ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ: " + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"НЕ ПРОЙДЕНО: {len(FAILS)}"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
