"""Подсказки при вводе: клиент видит, что не так, сразу, а не после отправки формы (зоотакси и общая форма).
Запуск:  python3 tests/e2e_live_hints.py   (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_live.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402

FAILS = []


def check(cond, name):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


async def hint(page, field):
    loc = page.locator(f"[data-live-for='{field}']")
    return (await loc.inner_text()).strip() if await loc.count() and await loc.is_visible() else ""


async def main():
    await db.init_db()
    app = webserver.make_app()
    app["bot"] = object()
    server = TestServer(app)
    await server.start_server()
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            page = await b.new_page(viewport={"width": 390, "height": 844})
            page.on("pageerror", lambda e: print("PAGEERR", e))
            sent = []
            page.on("request", lambda r: sent.append(r.url) if r.method == "POST" and r.url.split("?")[0].endswith("/request") else None)
            base = f"http://{server.host}:{server.port}"
            await page.goto(base + "/request?service=taxi")
            await page.fill("#phone", "123")
            await page.press("#phone", "Tab")
            check("номер" in await hint(page, "phone"), "кривой телефон: подсказка сразу после поля")
            await page.fill("#phone", "8 900 123-45-67")
            check(await hint(page, "phone") == "", "телефон поправлен: подсказка исчезла")
            await page.fill("#address", "Ленина")
            await page.press("#address", "Tab")
            check("дом" in await hint(page, "address"), "адрес без номера дома: подсказка")
            await page.fill("#address", "Москва, Ленина 5")
            await page.fill("#dest", "Москва, Ленина 5")
            await page.press("#dest", "Tab")
            check("совпада" in await hint(page, "dest"), "«Куда» = «Откуда»: подсказка")
            await page.fill("#name", "x")
            await page.fill("#name", "")
            await page.press("#name", "Tab")
            check(await hint(page, "name") != "", "пустое имя: подсказка")
            await page.fill("#when_date", "2020-01-01")
            await page.press("#when_date", "Tab")
            check("прошла" in await hint(page, "when_date"), "прошедшая дата: подсказка")
            await page.click("button[type=submit]", timeout=3000)
            await page.wait_for_timeout(300)
            check(not sent, "с ошибками форма не уходит на сервер")
            
            check(await hint(page, "consent") != "", "без согласия: подсказка у галочки")
            check(await page.evaluate("document.activeElement && document.activeElement.id") != "", "фокус на первом проблемном поле")
            await page.goto(base + "/request?service=taxi")
            sent.clear()
            await page.fill("#when_date", "2099-01-01")
            await page.fill("#address", "Москва, Ленина 5")
            await page.fill("#dest", "Москва, Тверская 1")
            await page.fill("#name", "[TEST] Иван")
            await page.fill("#phone", "8 900 123-45-67")
            await page.check("input[name=consent]")
            await page.click("button[type=submit]")
            await page.wait_for_timeout(800)
            check(len(sent) == 1, "верная форма уходит на сервер (один раз)")
            await page.goto(base + "/request?service=walk")
            await page.fill("#comment", "бл*дь, ну и двор")
            await page.press("#comment", "Tab")
            check("нецензурн" in await hint(page, "comment"), "мат в комментарии: подсказка под полем")
            await page.fill("#comment", "добрый пёс")
            await page.press("#comment", "Tab")
            check(await hint(page, "comment") == "", "комментарий поправлен: подсказка исчезла")
            await page.fill("#pet", "х у й")
            await page.press("#pet", "Tab")
            check("нецензурн" in await hint(page, "pet"), "мат в поле питомца: подсказка")
            import profanity
            words = ["хуй", "х.у.й", "xuy", "п*здец", "ребёнок", "хлеб", "Себастьян", "вебинар", "заебал", "бляяя",
                     "ёбаный", "сука", "Ленина 5", "нахуй", "oхуел", "fuck", "мандарин", "залупа", "ХУУУЙ", "блин"]
            got = await page.evaluate("w=>w.map(x=>window.zcProf(x))", words)
            check(got == [profanity.has_profanity(w) for w in words], "браузер и сервер одинаково определяют мат")
            await page.goto(base + "/request")
            await page.fill("#phone", "12")
            await page.press("#phone", "Tab")
            check("номер" in await hint(page, "phone"), "общая форма: кривой телефон")
            await b.close()
    finally:
        await server.close()
        await db.close_shared()
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
