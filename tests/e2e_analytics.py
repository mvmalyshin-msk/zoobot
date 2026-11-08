"""Счётчик посещений в настоящем Chromium: зашли на главную, прокрутили, нажали «заявка», ушли — события легли в базу.

Запуск:  BOT_TOKEN=1:x ADMIN_IDS=111 python tests/e2e_analytics.py   (нужен Playwright)"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_analytics.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp import web  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

FAILS = []


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=0, type="private"))


def check(title, good):
    print(("  ✅ " if good else "  ❌ ") + title)
    if not good:
        FAILS.append(title)


async def main():
    await db.init_db()
    app = webserver.make_app()
    app["bot"], app["dp"] = Bot("1:x", session=webchat.WebRouterSession(Inner())), None
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        ctx = await br.new_context(viewport={"width": 390, "height": 700}, user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1")
        pg = await ctx.new_page()
        await pg.goto(base + "/")
        await pg.mouse.wheel(0, 5000)
        await asyncio.sleep(1.5)
        link = pg.locator('a[href="/request"]').first
        await link.click()
        await pg.wait_for_url("**/request")
        await asyncio.sleep(0.5)
        await pg.focus('input[name="phone"]')
        await asyncio.sleep(0.5)
        await pg.close()          # уход со страницы: pagehide → leave
        await asyncio.sleep(1.5)
        await br.close()
    rows = await db.fetch_all("SELECT * FROM analytics_events WHERE source='web' ORDER BY id")
    kinds = [r["type"] for r in rows]
    check("просмотр главной записан", any(r["type"] == "pv" and r["path"] == "/" for r in rows))
    check("просмотр формы записан", any(r["type"] == "pv" and r["path"] == "/request" for r in rows))
    check("прокрутка записана", "scroll" in kinds)
    check("нажатие записано с адресом, куда ведёт", any(r["type"] == "click" and "/request" in (r["target"] or "") for r in rows))
    check("начатое поле формы записано по имени", any(r["type"] == "focus" and r["target"] == "phone" for r in rows))
    check("уход со страницы записан", "leave" in kinds)
    check("устройство определено как телефон", all(r["device"] == "mobile" for r in rows))
    check("одна сессия и один посетитель", len({r["sid"] for r in rows}) == 1 and len({r["vid"] for r in rows}) == 1)
    await runner.cleanup()
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО {len(FAILS)}: {FAILS}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
