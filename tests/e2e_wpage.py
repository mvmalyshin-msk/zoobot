"""Страница карты в настоящем Chromium. Внешние сайты (карты, подсказки, Telegram) подменены, сеть не нужна.

Запуск:  python tests/e2e_wpage.py   (нужен Playwright)
"""
import asyncio
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import webserver  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")
TG_STUB = """window.Telegram={WebApp:{ready(){},expand(){},sendData(d){window.__sent=d}}};"""
PHOTON = {"features": [
    {"geometry": {"coordinates": [37.3, 55.9]}, "properties": {"street": "Железнодорожная улица", "housenumber": "33",
                                                               "district": "Сходня", "city": "Химки", "state": "Московская область"}},
    {"geometry": {"coordinates": [30.3, 59.9]}, "properties": {"street": "Невский проспект", "housenumber": "1",
                                                              "city": "Санкт-Петербург", "state": "Санкт-Петербург"}},
    {"geometry": {"coordinates": [37.3, 55.91]}, "properties": {"street": "Железнодорожная улица", "city": "Химки",
                                                               "state": "Московская область"}},
]}
NOMINATIM = {"name": "", "address": {"road": "Тверская улица", "house_number": "7", "suburb": "Тверской район", "city": "Москва"}}
FAILS = []


def check(title, cond):
    print(("  ✅ " if cond else "  ❌ ") + title)
    if not cond:
        FAILS.append(title)


async def main():
    runner = web.AppRunner(webserver.make_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    photon_calls = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 390, "height": 760})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))

        async def route(r):
            u = r.request.url
            if "telegram.org" in u:
                await r.fulfill(body=TG_STUB, content_type="application/javascript")
            elif "photon.komoot.io" in u:
                photon_calls.append(u)
                await r.fulfill(body=json.dumps(PHOTON), content_type="application/json")
            elif "nominatim.openstreetmap.org" in u:
                await r.fulfill(body=json.dumps(NOMINATIM), content_type="application/json")
            elif "tile.openstreetmap.org" in u:
                await r.fulfill(body=PNG, content_type="image/png")
            elif u.startswith(f"http://127.0.0.1:{port}"):
                await r.continue_()
            else:
                await r.abort()
        await pg.route("**/*", route)
        await pg.goto(f"http://127.0.0.1:{port}/")
        await pg.wait_for_selector(".leaflet-container")

        check("кнопка «Выбрать» выключена, пока адрес не выбран", await pg.is_disabled("#ok"))

        # --- подсказки при наборе ---
        await pg.fill("#q", "ж")
        await pg.wait_for_timeout(600)
        check("на 1 букву подсказки не запрашиваются", not photon_calls)
        await pg.fill("#q", "железнодорожная 33")
        await pg.wait_for_selector("#sugg button")
        items = await pg.locator("#sugg button").all_inner_texts()
        check("подсказки пришли", len(items) == 2)
        check("чужой город (Санкт-Петербург) отфильтрован", not any("Невский" in t for t in items))
        check("подсказка с домом читается: улица, дом, район, город",
              "Железнодорожная улица, 33, Сходня, Химки" in items[0])
        check("подсказка без дома предупреждает", "дом не указан" in items[1])
        await pg.locator("#sugg button").first.click()
        check("после выбора подсказки адрес в поле", await pg.input_value("#label") == "Железнодорожная улица, 33, Сходня, Химки")
        check("кнопка «Выбрать» включилась", not await pg.is_disabled("#ok"))

        # --- квартира и отправка в бота ---
        await pg.fill("#flat", "238")
        await pg.click("#ok")
        sent = json.loads(await pg.evaluate("window.__sent"))
        check("в бот ушли координаты, адрес и квартира",
              abs(sent["lat"] - 55.9) < 1e-6 and sent["label"].startswith("Железнодорожная") and sent["flat"] == "238")

        # --- выбор точки касанием по карте ---
        await pg.evaluate("window.__sent=null")
        await pg.click("#map", position={"x": 150, "y": 100})
        await pg.wait_for_function("document.getElementById('label').value.length > 0")
        check("касание карты подставляет адрес из обратного геокодирования",
              await pg.input_value("#label") == "Тверская улица, 7, Тверской район, Москва")
        # --- адрес без дома нельзя отправить ---
        await pg.fill("#label", "Тверская улица")
        check("без номера дома отправить нельзя", await pg.is_disabled("#ok"))
        check("страница подсказывает дописать дом", "номер дома" in await pg.inner_text("#hint"))
        check("в консоли страницы нет ошибок JS", not errors)
        await b.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {FAILS}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
