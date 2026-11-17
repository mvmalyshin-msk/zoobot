"""Админка в Chromium (390 px, как на iPhone): удаление клиента через окно «Вы уверены?», форма исполнителя
(название/ФИО по типу, создание, правка, удаление), кнопка звонка с номером клиента.
Запуск:  python3 tests/e2e_portal_admin.py   (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_padmin.db"
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
    uid = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?,?,?,1)", (880001, "Мусор Тестов", "+79778164660"))
    await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?,?,?,1)", (880002, "Живой Клиент", "+79001112233"))
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    oid = await db.execute("INSERT INTO orders (user_id, service_id, status, start_at, quantity, address, price_total, express) VALUES (?,?,?,?,?,?,?,1)",
                           (uid, sid, "created", "2026-12-01 10:00:00", 1, "Мира 1", 900))
    app = webserver.make_app()
    app["bot"] = None
    server = TestServer(app)
    await server.start_server()
    base = f"http://{server.host}:{server.port}"
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch()
            ctx = await b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": await webchat.new_session(111), "url": base}])
            page = await ctx.new_page()
            page.on("pageerror", lambda e: print("PAGEERR", e))

            # --- звонок: номер на кнопке = номер клиента
            await page.goto(f"{base}/portal/order/{oid}")
            call = page.locator('a[href^="tel:"]')
            check(await call.count() == 1, "в карточке заказа одна кнопка звонка")
            check("+7 (977) 816-46-60" in (await call.inner_text()), "на кнопке звонка написан номер клиента")
            check(await call.get_attribute("href") == "tel:+79778164660", "набирается тот же номер")
            await page.goto(f"{base}/portal")
            check(await page.locator(".item.ord.express").count() >= 1, "экспресс-заказ бургундский в списке")

            # --- удаление клиента: сначала окно с вопросом, «Отмена» ничего не удаляет
            await page.goto(f"{base}/portal/clients")
            check(not await page.evaluate("document.documentElement.scrollWidth>window.innerWidth"), "список клиентов без горизонтальной прокрутки")
            trash = page.locator(f'[data-del="{uid}"]')
            box = await trash.bounding_box()
            check(box["width"] >= 44 and box["height"] >= 44, "кнопка 🗑 не меньше 44 px")
            check(not await page.locator("#delmodal").is_visible(), "окно подтверждения закрыто, пока не нажали 🗑")
            await trash.click()
            check(await page.locator("#delmodal").is_visible(), "после 🗑 открылось окно")
            txt = await page.locator("#delmodal").inner_text()
            check("Вы уверены, что хотите удалить клиента?" in txt and "Мусор Тестов" in txt, "в окне вопрос и имя клиента")
            await page.click("#delno")
            check(not await page.locator("#delmodal").is_visible(), "«Отмена» закрывает окно")
            check(await db.fetch_one("SELECT 1 FROM users WHERE id=?", (uid,)) is not None, "после «Отмена» клиент на месте")
            await trash.click()
            await page.click("#delmodal .danger")
            await page.wait_for_load_state()
            check("незавершённые заказы" in await page.inner_text("main"), "клиента с активным заказом удалить нельзя, сказано почему")
            await db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (oid,))
            await page.goto(f"{base}/portal/clients")
            await page.locator(f'[data-del="{uid}"]').click()
            await page.click("#delmodal .danger")
            await page.wait_for_load_state()
            check(await db.fetch_one("SELECT 1 FROM users WHERE id=?", (uid,)) is None, "после «Да, удалить» клиента нет")
            check("Мусор Тестов" not in await page.inner_text("main"), "в списке его больше нет")
            check("Живой Клиент" in await page.inner_text("main"), "остальные клиенты на месте")

            # --- исполнители
            await page.goto(f"{base}/portal/executors")
            check("/add_executor" not in await page.inner_text("main"), "подсказки про команду бота больше нет")
            await page.click("text=Добавить исполнителя")
            check(await page.locator("#namelbl").inner_text() == "Название организации", "по умолчанию тип «Организация» и поле «Название организации»")
            await page.check('input[name=kind][value=person]')
            check(await page.locator("#namelbl").inner_text() == "ФИО", "для «Физлицо» поле называется «ФИО»")
            check(await page.locator("#personbox").is_visible(), "для физлица виден налоговый статус")
            await page.check('input[name=kind][value=org]')
            check(not await page.locator("#personbox").is_visible(), "для организации налогового статуса нет")
            await page.fill("#name", "ООО Лапки")
            await page.fill("#phone", "8 900 222-33-44")
            await page.fill("#links", "@lapki_msk\nhttps://vk.com/lapki")
            await page.check('input[name=services][value=walk]')
            check(not await page.evaluate("document.documentElement.scrollWidth>window.innerWidth"), "форма без горизонтальной прокрутки")
            await page.click("text=Сохранить")
            await page.wait_for_load_state()
            ex = await db.fetch_one("SELECT * FROM executors WHERE name='ООО Лапки'")
            check(ex is not None and ex["legal_status"] == "legal" and ex["phone"] == "+79002223344", "организация сохранена")
            check(ex["links"] == "https://t.me/lapki_msk\nhttps://vk.com/lapki", "ссылки сохранены и приведены к виду https://")
            check("ООО Лапки" in await page.inner_text("main"), "в списке появилась карточка")

            await page.click("a.item:has-text(\"ООО Лапки\")")
            await page.select_option("#status", "0")
            await page.click("text=Сохранить")
            await page.wait_for_load_state()
            check((await db.fetch_one("SELECT active FROM executors WHERE id=?", (ex["id"],)))["active"] == 0, "статус «Неактивен» сохранён")
            check("неактивен" in await page.inner_text("main"), "в списке написано «неактивен»")

            await page.click("a.item:has-text(\"ООО Лапки\")")
            await page.click("#exopen")
            check("Вы уверены, что хотите удалить исполнителя?" in await page.locator("#exdel").inner_text(), "перед удалением исполнителя спрашивают")
            await page.click("#exno")
            check(await db.fetch_one("SELECT 1 FROM executors WHERE id=?", (ex["id"],)) is not None, "«Отмена» не удаляет")
            await page.click("#exopen")
            await page.click("#exdel .danger")
            await page.wait_for_load_state()
            check(await db.fetch_one("SELECT 1 FROM executors WHERE id=?", (ex["id"],)) is None, "«Да, удалить» удаляет исполнителя")

            # --- ошибка формы: поля сохраняются, ошибка видна
            await page.goto(f"{base}/portal/executors/new")
            await page.fill("#name", "Без телефона")
            await page.check('input[name=services][value=taxi]')
            await page.click("text=Сохранить")
            await page.wait_for_load_state()
            check(await page.input_value("#name") == "Без телефона", "после ошибки введённое имя осталось")
            await b.close()
    finally:
        await server.close()
    print("FAILED:", FAILS if FAILS else "none")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
