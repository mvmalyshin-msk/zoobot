"""Портал и чат в настоящем Chromium на ширине айфона: страницы открываются, без ошибок JS и без горизонтальной
прокрутки, переписка из портала доходит клиенту. Запуск: python3 tests/e2e_portal.py [папка для скриншотов]"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_e2e_portal.db")
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

FAILS, SENT = [], []
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else None


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...
    async def make_request(self, bot, method, timeout=None):
        SENT.append(method)
        return True


async def shot(page, name):
    if SHOTS:
        SHOTS.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


async def no_hscroll(page, name):
    w = await page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    check(w <= 1, f"{name}: нет горизонтальной прокрутки ({w}px)")


async def main():
    await db.init_db()
    uid = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (5555, 'Ирина', '+79001112233', 1)")
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='taxi' LIMIT 1"))["id"]
    oid = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, address, address_to, rider_name, rider_phone) "
                           "VALUES (?,?, '2030-01-01 10:00:00', 1820, 'Садовническая 3', 'Брюсов пер. 2', 'Борис', '+79165554433')", (uid, sid))
    await db.execute("INSERT INTO payments (order_id, amount) VALUES (?, 1820)", (oid,))
    await db.execute("INSERT INTO web_requests (service_id, name, phone, address, when_text) VALUES (?, 'Фёдор', '+79007778899', 'Мира 1', 'завтра 10:00')", (sid,))
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
        ctx = await br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        errors = []
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/portal")
        check("Вход в портал" in await page.inner_text("body"), "без входа — страница входа")
        await shot(page, "portal-login")
        await no_hscroll(page, "вход")
        token = await webchat.new_session(111)
        await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": token, "url": base}])
        await page.goto(base + "/portal?f=all")
        body = await page.inner_text("body")
        check(f"№{oid}" in body and "Фёдор" in body and "Форма на сайте" in body, "список: заказ из бота и заявка с сайта")
        font = await page.evaluate("getComputedStyle(document.body).fontFamily")
        check("PT Sans" in font, "фирменный шрифт PT Sans: " + font)
        bg = await page.evaluate("getComputedStyle(document.body).backgroundColor")
        check(bg == "rgb(253, 246, 236)", "фирменный кремовый фон: " + bg)
        await shot(page, "portal-list")
        await no_hscroll(page, "список")
        await page.click(f"a[href='/portal/order/{oid}']")
        check("Борис" in await page.inner_text("body"), "карточка: кто поедет в такси")
        await shot(page, "portal-order")
        await no_hscroll(page, "карточка")
        await page.click("text=✅ Подтвердить")
        await page.wait_for_load_state()
        st = (await db.fetch_one("SELECT status FROM orders WHERE id=?", (oid,)))["status"]
        check(st == "confirmed", "кнопка «Подтвердить» в портале подтверждает заказ")
        await page.goto(base + "/portal/chat/5555")
        await page.fill("#st", "Здравствуйте, Ирина!")
        await page.press("#st", "Enter")
        await page.wait_for_selector(".msg.out", timeout=5000)
        check(any(getattr(m, "chat_id", None) == 5555 and "Здравствуйте, Ирина!" in (m.text or "") for m in SENT),
              "сообщение из портала ушло клиенту")
        await shot(page, "portal-chat")
        await no_hscroll(page, "чат")
        cp = await ctx.new_page()
        cp.on("pageerror", lambda e: errors.append(str(e)))
        await webchat.push(111, "send", who="bot", text="🔐 Код входа в портал: 123456")
        await cp.goto(base + "/portal/bot")  # чат администратора с ботом — в портале
        await cp.wait_for_selector(".m.bot", timeout=8000)
        check("АДМИНИСТРАТОР" in await cp.inner_text(".role-ribbon"), "чат админа в портале — красная лента")
        await shot(cp, "chat")
        await no_hscroll(cp, "чат-бот")
        # тот же браузер, клиентский чат: админ и клиент не сливаются (скриншот владельца)
        kp = await ctx.new_page()
        kp.on("pageerror", lambda e: errors.append(str(e)))
        await kp.goto(base + "/chat")
        await kp.wait_for_selector(".m.bot", timeout=8000)
        ktext = await kp.inner_text("body")
        check("КЛИЕНТСКОЕ МЕНЮ" in await kp.inner_text(".role-ribbon") and "Код входа в портал" not in ktext,
              "в том же браузере клиентский чат — синий и без сообщений админа")
        await cp.reload()
        await cp.wait_for_selector(".m.bot", timeout=8000)
        check("Код входа в портал" in await cp.inner_text("body"), "после открытия клиентского чата админ остался админом")
        icon = await cp.evaluate("document.querySelector('link[rel=apple-touch-icon]').href")
        r = await cp.request.get(icon)
        check(r.ok, "иконка для экрана «Домой» отдаётся")
        check(not errors, "без ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {FAILS}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
