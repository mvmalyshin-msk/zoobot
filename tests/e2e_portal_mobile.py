"""Карточка заявки в админке на телефоне (Chromium, 390 px): нет горизонтальной прокрутки, кнопки и поля не меньше 44 px,
поля формы на всю ширину, «Когда начать» заполнено, кнопки не съезжают.
Запуск:  python3 tests/e2e_portal_mobile.py   (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_pmobile.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

FAILS = []


def check(cond, name):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


async def main():
    await db.init_db()
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    rid = await db.execute("INSERT INTO web_requests (service_id, name, phone, address, when_text, web_uid, express, comment) VALUES (?,?,?,?,?,?,1,?)",
                           (sid, "Анна", "+79001112233", "Химки, Победы 3", "17.11.2026, 12:30 · ⚡ Экспресс", 910_000_003, "домофон 12"))
    app = webserver.make_app()
    app["bot"] = None
    server = TestServer(app)
    await server.start_server()
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            tok = await webchat.new_session(111)
            await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": tok, "url": f"http://{server.host}:{server.port}"}])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: print("PAGEERR", e))
            await page.goto(f"http://{server.host}:{server.port}/portal/request/{rid}")
            check(not await page.evaluate("document.documentElement.scrollWidth>window.innerWidth"), "нет горизонтальной прокрутки")
            check(await page.input_value("#start") == "2026-11-17T12:30", "«Когда начать» уже заполнено временем заявки")
            small = await page.evaluate("""[...document.querySelectorAll('main .btn, main select, main input:not([type=hidden]), main textarea')]
                .filter(e=>e.offsetParent&&e.getBoundingClientRect().height<44).map(e=>(e.id||e.textContent||e.tagName).trim().slice(0,30))""")
            check(not small, f"все кнопки и поля не ниже 44 px: {small}")
            wide = await page.evaluate("""[...document.querySelectorAll('form.card input:not([type=hidden]), form.card select, form.card textarea')]
                .filter(e=>e.offsetParent).filter(e=>e.getBoundingClientRect().width<e.closest('form').getBoundingClientRect().width-40).map(e=>e.id||e.name)""")
            check(not wide, f"поля форм на всю ширину карточки: {wide}")
            b1 = await page.evaluate("(()=>{const q=document.getElementById('qty').getBoundingClientRect(),m=document.querySelector('button[name=make_order]').getBoundingClientRect();return m.top>=q.bottom})()")
            check(b1, "«Оформить заказ» под полем количества, а не рядом")
            check(await page.locator(".reg.express").count() == 1, "бургундская плашка экспресса на месте")
            await page.screenshot(path="/tmp/claude-0/pmobile.png", full_page=True)
            await b.close()
    finally:
        await server.close()
    print("\nИТОГО: %s" % ("всё зелёное" if not FAILS else "падений: %d" % len(FAILS)))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
