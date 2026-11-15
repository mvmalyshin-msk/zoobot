"""Подсказки адреса в заявке на зоотакси в настоящем Chromium: клиент пишет адрес, видит варианты, выбирает.
Карта (geo.search) подменена, сеть не нужна.   Запуск:  python3 tests/e2e_addr_suggest.py   (нужен Playwright)"""
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
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_addr.db"

from aiohttp.test_utils import TestServer  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import webserver  # noqa: E402

LABELS = ["Ленина улица, 5, Тверской, Москва", "Ленина улица, 7, Тверской, Москва"]
PHOTON = {"features": [
    {"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "housenumber": "5", "district": "Тверской",
                                                              "city": "Москва", "state": "Москва"}},
    {"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "city": "Москва", "state": "Москва"}},
    {"geometry": {"coordinates": [37.6, 55.7]}, "properties": {"street": "Ленина улица", "housenumber": "7", "district": "Тверской",
                                                              "city": "Москва", "state": "Москва"}},
    {"geometry": {"coordinates": [30.3, 59.9]}, "properties": {"street": "Ленина улица", "housenumber": "1",
                                                              "city": "Санкт-Петербург", "state": "Санкт-Петербург"}},
]}
STREET_ONLY = {"features": [PHOTON["features"][1]]}
FAILS = []


def check(cond, name):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)


async def fake_search(text):
    return [geo.Place(label=l, district="Москва", lat=55.7, lon=37.6, outside_mkad=False, has_house=True) for l in LABELS]


async def main():
    await db.init_db()
    with mock.patch.object(geo, "search", side_effect=fake_search):
        app = webserver.make_app()
        app["bot"] = object()
        server = TestServer(app)
        await server.start_server()
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch()
                page = await b.new_page()
                answer = {"data": PHOTON}

                async def photon(route):
                    await route.fulfill(status=200, content_type="application/json", headers={"access-control-allow-origin": "*"},
                                        body=json.dumps(answer["data"]))
                await page.route("https://photon.komoot.io/**", photon)
                await page.goto(f"http://{server.host}:{server.port}/request?service=taxi")
                await page.fill("#address", "ленина 5")
                await page.wait_for_selector("#address-sug .sug", timeout=5000)
                items = await page.locator("#address-sug .sug").all_inner_texts()
                check(items == LABELS, f"показаны только адреса с домом, и только Москва/область: {items}")
                await page.click("#address-sug .sug >> nth=0")
                check(await page.input_value("#address") == LABELS[0], "выбранный адрес лёг в поле")
                check(await page.input_value("input[name=address_ok]") == LABELS[0], "выбор запомнен для проверки на сервере")
                check(await page.locator("#address-sug .sug").count() == 0, "список закрылся после выбора")
                await page.fill("#address", LABELS[0] + "x")
                check(await page.input_value("input[name=address_ok]") == "", "правка адреса снимает отметку «выбран»")
                answer["data"] = STREET_ONLY
                await page.fill("#dest", "ленина улица")
                await page.wait_for_selector("#dest-sug .sug-note", timeout=5000)
                note = await page.locator("#dest-sug .sug-note").inner_text()
                check("номер дома" in note, f"для улицы без дома просит дописать номер: {note}")
                await page.fill("#address", LABELS[0])
                await page.fill("#name", "Анна")
                await page.fill("#phone", "8 900 777-88-99")
                await page.check("input[name=consent]")
                await page.fill("#when_date", (date.today() + timedelta(days=1)).isoformat())
                await page.click("button.btn[type=submit]")
                await page.wait_for_load_state()
                check("Укажите номер дома" in await page.content(), "улица без дома не отправляется")
                await b.close()
        finally:
            await server.close()
            await db.close_shared()
    print("ПРОВАЛЕНО: %d" % len(FAILS) if FAILS else "все проверки пройдены")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
