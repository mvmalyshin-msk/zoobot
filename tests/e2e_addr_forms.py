"""Единая строка адреса во всех формах заявки, в настоящем Chromium: подсказки, ошибка под строкой, геопозиция, карта с поправкой.
Внешние сервисы (Photon, Nominatim, плитки карты, Telegram) подменены.   Запуск:  python3 tests/e2e_addr_forms.py"""
import asyncio
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_addr_forms.db"

from aiohttp.test_utils import TestServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import webserver  # noqa: E402

LABEL = "Ленина улица, 5, Тверской, Москва"
SEARCH = {"features": [
    {"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "housenumber": "5", "district": "Тверской",
                                                              "city": "Москва", "state": "Москва"}},
    {"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "housenumber": "7", "district": "Тверской",
                                                              "city": "Москва", "state": "Москва"}}]}
HOUSE = {"features": [SEARCH["features"][0]]}
STREET = {"features": [{"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "city": "Москва", "state": "Москва"}}]}
FAILS = []


def check(cond, name):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


async def fake_search(text):
    if "слезам" in text or "Абвгд" in text:
        return []
    return [geo.Place(label=LABEL, district="Москва", lat=55.7, lon=37.6, outside_mkad=False, has_house=True)]


async def main():
    await db.init_db()
    state = {"search": SEARCH, "reverse": HOUSE}
    with mock.patch.object(geo, "search", side_effect=fake_search):
        app = webserver.make_app()
        app["bot"] = object()
        server = TestServer(app)
        await server.start_server()
        base = f"http://{server.host}:{server.port}"
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch()
                ctx = await b.new_context(geolocation={"latitude": 55.7, "longitude": 37.6}, permissions=["geolocation"])

                async def photon(route):
                    body = state["reverse"] if "/reverse" in route.request.url else state["search"]
                    await route.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                                        body=json.dumps(body))
                await ctx.route("https://photon.komoot.io/**", photon)
                await ctx.route("https://nominatim.openstreetmap.org/**", lambda r: r.fulfill(
                    status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                    body=json.dumps({"name": "x", "address": {"road": "Ленина улица", "house_number": "5", "city": "Москва"}})))
                await ctx.route("https://tile.openstreetmap.org/**", lambda r: r.abort())
                async def hang(route):                                   # telegram.org не отвечает: карта в окне всё равно должна открыться
                    await asyncio.sleep(3600)
                await ctx.route("https://telegram.org/**", hang)
                errors = []

                for url, label in ((f"{base}/request?service=walk", "выгул"), (f"{base}/request?service=sitter", "зоонаня"),
                                   (f"{base}/request?service=taxi", "такси"), (f"{base}/request?service=boarding", "передержка"),
                                   (f"{base}/request", "общая форма")):
                    page = await ctx.new_page()
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    await page.goto(url)
                    if url.endswith("/request"):
                        await page.select_option("#service_id", index=1)
                    check(await page.locator("[data-act=locate]").count() >= 1, f"{label}: есть «Определить по геопозиции»")
                    check(await page.locator("[data-act=map]").count() >= 1, f"{label}: есть «Уточнить на карте»")
                    state["search"] = SEARCH
                    await page.fill("#address", "ленина 5")
                    await page.wait_for_selector("#address-sug .sug", timeout=5000)
                    check(await page.locator("#address-sug .sug").count() == 2, f"{label}: выпадающий список адресов")
                    await page.click("#address-sug .sug >> nth=0")
                    check(await page.input_value("#address") == LABEL, f"{label}: выбранный адрес лёг в строку")
                    check(await page.input_value("input[name=address_ok]") == LABEL, f"{label}: выбор запомнен")
                    await page.close()

                # выдуманный адрес: ошибка прямо под строкой, форма не уходит
                page = await ctx.new_page()
                await page.goto(f"{base}/request?service=walk")
                await page.fill("#address", "Москва слезам не верит")
                await page.fill("#name", "Анна")
                await page.fill("#phone", "8 900 777-88-99")
                await page.check("input[name=consent]")
                await page.fill("#when_date", (date.today() + timedelta(days=2)).isoformat())
                await page.wait_for_function("document.querySelectorAll('.slot:not([disabled])').length>3")
                await page.locator(".slot:not([disabled])").last.click()
                await page.click("button.btn[type=submit]")
                msg = await page.locator("[data-live-for=address]").inner_text()
                check("номер дома" in msg, f"ошибка под строкой адреса: {msg}")
                await page.fill("#address", "Абвгд 55")
                await page.click("button.btn[type=submit]")
                await page.wait_for_load_state()
                check("Не нашёл такой адрес" in await page.content(), "адрес с цифрами, которого нет на карте, сервер не принимает")
                check(await page.locator(".addr .err").count() == 1, "ошибка сервера стоит под строкой адреса")
                await page.close()

                # Photon нашёл только улицу, а второй источник (сервер) знает дом — клиент всё равно получает подсказку
                page = await ctx.new_page()
                await page.goto(f"{base}/request?service=walk")
                state["search"] = STREET
                await page.fill("#address", "Москва Тверская Ямская 16/23")
                await page.wait_for_selector("#address-sug .sug", timeout=6000)
                check(await page.locator("#address-sug .sug").first.get_attribute("data-pick") == LABEL, "второй источник подсказывает адрес, когда первый нашёл только улицу")
                state["search"] = SEARCH
                # оскорбление в комментарии и выдуманный номер: ошибка сразу под полем
                await page.fill("#comment", "Жопа")
                await page.fill("#phone", "8 987 123-45-67")
                await page.fill("#name", "Анна")
                await page.click("#name")
                msgs = [await el.inner_text() for el in await page.locator("[data-live-for]").all()]
                check(any("оскорбительные" in m for m in msgs), f"оскорбление в комментарии отмечено: {msgs}")
                check(any("выдуманным" in m for m in msgs), "выдуманный номер отмечен")
                await page.close()

                # геопозиция: дом найден → строка заполнена; только улица → просит дописать дом
                page = await ctx.new_page()
                await page.goto(f"{base}/request?service=walk")
                state["reverse"] = HOUSE
                await page.click("[data-act=locate]")
                await page.wait_for_function("document.querySelector('#address').value.length>0", timeout=5000)
                check(await page.input_value("#address") == LABEL, "геопозиция: адрес с домом в строке")
                check(await page.input_value("input[name=address_ok]") == LABEL, "геопозиция: адрес считается выбранным")
                await page.fill("#address", "")
                state["reverse"] = STREET
                await page.click("[data-act=locate]")
                await page.wait_for_selector(".addr-note.bad", timeout=5000)
                note = await page.locator(".addr-note").first.inner_text()
                check("номер дома" in note, f"геопозиция нашла только улицу: {note}")
                check(await page.input_value("input[name=address_ok]") == "", "улица без дома не считается выбранным адресом")
                await page.close()

                # геопозиция запрещена: понятное сообщение
                ctx2 = await b.new_context()
                page = await ctx2.new_page()
                await page.goto(f"{base}/request?service=walk")
                await page.click("[data-act=locate]")
                await page.wait_for_selector(".addr-note.bad", timeout=15000)
                check("Не удалось определить" in await page.locator(".addr-note").first.inner_text()
                      or "геопозиции" in await page.locator(".addr-note").first.inner_text(), "геопозиция недоступна: есть объяснение")
                await ctx2.close()

                # карта в окне: метка двигается, адрес возвращается в строку
                page = await ctx.new_page()
                await ctx.route("https://nominatim.openstreetmap.org/**", lambda r: r.fulfill(
                    status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                    body=json.dumps({"name": "x", "address": {"road": "Ленина улица", "house_number": "5", "city": "Москва"}})))
                await page.goto(f"{base}/request?service=walk")
                await page.click("[data-act=map]")
                await page.wait_for_selector(".zc-modal iframe", timeout=5000)
                frame = page.frame_locator(".zc-modal iframe")
                await frame.locator("#map").wait_for(timeout=5000)
                f = [x for x in page.frames if "/map" in x.url][0]
                await f.wait_for_function("window.__test", timeout=5000)
                await f.evaluate("() => window.__test.setPoint(55.7, 37.6, '', null)")
                check(await f.evaluate("() => !!document.querySelector('.leaflet-marker-draggable')"), "на карте метка, которую можно перетащить")
                await f.fill("#label", "Ленина улица, 5, Москва")
                await f.click("#ok")
                await page.wait_for_selector(".zc-modal", state="detached", timeout=5000)
                check(await page.input_value("#address") == "Ленина улица, 5, Москва", "точка с карты вернулась в строку адреса")
                check(await page.input_value("input[name=address_ok]") == "Ленина улица, 5, Москва", "адрес с карты считается выбранным")
                await page.close()

                check(not errors, f"в консоли страниц нет ошибок: {errors}")
                await b.close()
        finally:
            await server.close()
            await db.close_shared()
    print("ПРОВАЛЕНО: %d" % len(FAILS) if FAILS else "ИТОГ: ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
