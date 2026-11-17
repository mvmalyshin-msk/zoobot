"""Сетка времени в форме заявки (Chromium, телефон): недоступное затемнено и не нажимается, «Экспресс» пересчитывает сетку сразу,
ночью экспресса нет, объяснение подстраивается под ситуацию, «?» открывает подсказку.
Запуск:  python3 tests/e2e_time_grid.py   (нужен Playwright)"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_timegrid.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

FAILS = []
NOW = {"v": None}
JS_ENABLED = "[...document.querySelectorAll('.slot')].some(e=>e.textContent==='%s'&&!e.disabled)"
JS_DISABLED = "[...document.querySelectorAll('.slot')].some(e=>e.textContent==='%s'&&e.disabled)"


def check(cond, name):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


def at(h, m=0):
    return datetime.now(config.tz).replace(hour=h, minute=m, second=0, microsecond=0)


async def chip(page, t):
    return page.locator(f".slot:text-is('{t}')")


async def main():
    await db.init_db()
    webform._now = lambda: NOW["v"]
    app = webserver.make_app()
    app["bot"] = object()
    server = TestServer(app)
    await server.start_server()
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: print("PAGEERR", e))
            base = f"http://{server.host}:{server.port}"

            NOW["v"] = at(12, 0)
            await page.goto(base + "/request?service=walk")
            await page.wait_for_selector(".slot")
            check(await page.locator(".slot").count() == 33, "сетка 06:00–22:00 с шагом 30 минут: 33 ячейки")
            c13, c14 = await chip(page, "13:00"), await chip(page, "14:00")
            check(await c13.is_disabled(), "13:00 при «сейчас 12:00» затемнено и недоступно")
            check(await c14.is_enabled(), "14:00 доступно")
            op = await c13.evaluate("e=>getComputedStyle(e).opacity")
            check(float(op) < 0.6, "недоступное время полупрозрачное")
            await c13.click(force=True)
            check(await page.input_value("#when_time") == "", "по недоступному нажатие ничего не ставит")
            await c14.click()
            check(await page.input_value("#when_time") == "14:00", "доступное время выбирается")
            check(await page.locator(".slot.on").inner_text() == "14:00", "выбранное время подсвечено")

            c1230 = await chip(page, "12:30")
            check(await c1230.is_disabled(), "12:30 без «Экспресса» недоступно")
            await page.check("input[name=express]")
            await page.wait_for_function(JS_ENABLED % "12:30")
            check(await c1230.is_enabled(), "«Экспресс» включили: 12:30 сразу стало доступно, без перезагрузки")
            check(await (await chip(page, "12:00")).is_disabled(), "даже с «Экспрессом» 12:00 (уже сейчас) недоступно")
            check(await (await chip(page, "06:00")).is_disabled(), "прошедшее утро недоступно")
            await page.uncheck("input[name=express]")
            await page.wait_for_function(JS_DISABLED % "12:30")
            check(await (await chip(page, "12:30")).is_disabled(), "«Экспресс» сняли: сетка вернулась")

            await page.evaluate("document.getElementById('when_time').value='13:00';document.getElementById('when_time').dispatchEvent(new Event('input',{bubbles:true}))")
            await page.wait_for_selector("#slot-note button")
            txt = await page.inner_text("#slot-note")
            check("минимум 2 часа" in txt and "«Экспресс»" in txt, "выбрали слишком раннее: мягкое объяснение с предложением «Экспресс»")
            check(await page.locator("#slot-note button").inner_text() == "Подставить ближайшее время: 14:00", "кнопка «Подставить ближайшее время»")
            await page.click("#slot-note button")
            await page.wait_for_function("document.getElementById('when_time').value==='14:00'")
            check(await page.input_value("#when_time") == "14:00", "кнопка подставила ближайшее время")

            await page.click(".help[data-tip=time]")
            check(await page.locator("#tip-box").is_visible(), "«?» открывает подсказку")
            check("минимум 2 часа" in await page.inner_text("#tip-box"), "в подсказке сказано про 2 часа")
            overflow = await page.evaluate("document.documentElement.scrollWidth>window.innerWidth")
            check(not overflow, "нет горизонтальной прокрутки на телефоне")

            NOW["v"] = at(3, 10)
            await page.goto(base + "/request?service=walk")
            await page.wait_for_selector(".slot")
            check(await page.locator("input[name=express]").is_disabled(), "ночью «Экспресс» недоступен")
            check(await (await chip(page, "10:30")).is_disabled() and await (await chip(page, "11:00")).is_enabled(),
                  "ночью ближайшее время 11:00 (09:00 + 2 часа)")
            txt = await page.inner_text("#slot-note")
            check("с 09:00 утра" in txt and "11:00" in txt, "ночное объяснение: администраторы с 09:00, ближайшее 11:00")
            await b.close()
    finally:
        await server.close()
    print("\nИТОГО: %s" % ("всё зелёное" if not FAILS else "падений: %d" % len(FAILS)))
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
