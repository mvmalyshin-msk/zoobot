"""Портал в настоящем Chromium: открыт список «Заявки», клиент отменяет заказ — список обновляется сам, за несколько секунд.

Запуск:  BOT_TOKEN=1:x ADMIN_IDS=111 python tests/e2e_portal_live.py   (нужен Playwright)"""
import asyncio
import os
import secrets
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_portal_live.db"
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
    tg = 400_000_000 + secrets.randbelow(10**6)
    u = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, 'Живой', '+79001234567', 1)", (tg,))
    sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
    oid = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, status) VALUES (?,?, '2030-01-01 10:00:00', 910, 'created')", (u, sid))
    app = webserver.make_app()
    app["bot"], app["dp"] = Bot("1:x", session=webchat.WebRouterSession(Inner())), None
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    tok = await webchat.new_session(111)
    async with async_playwright() as pw:
        br = await pw.chromium.launch()
        ctx = await br.new_context()
        await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": tok, "url": f"http://127.0.0.1:{port}"}])
        pg = await ctx.new_page()
        await pg.goto(f"http://127.0.0.1:{port}/portal")
        row = pg.locator(f'a.item.ord[href="/portal/order/{oid}"]')
        check("новая заявка в списке — белая", "st-new" in (await row.get_attribute("class")))
        await asyncio.sleep(6)  # первый опрос запомнил текущее состояние
        await db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
        await db.execute("INSERT INTO order_statuses_log (order_id, from_status, to_status, actor) VALUES (?,?,?,?)",
                         (oid, "created", "cancelled", "client:5"))
        try:
            await pg.wait_for_selector(f'a.item.ord.st-cancelled[href="/portal/order/{oid}"]', timeout=15000)
            check("клиент отменил — список сам показал «Отменил клиент»", "Отменил клиент" in await pg.inner_text("body"))
        except Exception:
            check("клиент отменил — список сам обновился за 15 секунд", False)
        await br.close()
    await runner.cleanup()
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
