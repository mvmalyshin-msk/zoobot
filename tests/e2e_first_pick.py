"""Браузерный сценарий первого подбора и /start через тестовый транспорт Telegram."""
import asyncio
import html
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
HOME_PORT = 8789
BASE = f"http://127.0.0.1:{HOME_PORT}"
TEST_DB = tempfile.TemporaryDirectory(prefix="zoobot-first-pick-")
os.environ.update(BOT_TOKEN="1:test", ADMIN_IDS="111", DB_PATH=str(Path(TEST_DB.name) / "test.db"),
                  WEBAPP_URL=BASE, PYTHON_DOTENV_DISABLED="1")

from aiohttp import web
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from playwright.async_api import async_playwright, expect

import db
import geo
import orders
import webchat
import webserver
from bot import build_dispatcher
from config import config

SURVEY_URL = "https://forms.yandex.ru/u/6afa34231f1eb57dec4266f0/"


class TestTelegramSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.messages = []

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise AssertionError("Unexpected download")
        yield b""

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.messages.append(method)
        return True


async def start_bot(bot, session):
    dispatcher = build_dispatcher()
    user = {"id": 1234, "is_bot": False, "first_name": "Тест"}
    chat = {"id": user["id"], "type": "private"}
    now = int(datetime.now().timestamp())
    await dispatcher.feed_raw_update(bot, {"update_id": 1, "message": {
        "message_id": 1, "date": now, "chat": chat, "from": user, "text": "/start",
        "entities": [{"type": "bot_command", "offset": 0, "length": 6}],
    }})
    assert len(session.messages) == 1
    greeting = session.messages[0]
    buttons = [b for row in greeting.reply_markup.inline_keyboard for b in row]
    assert buttons[0].text == "Заполнить анкету" and buttons[0].url == SURVEY_URL
    assert [b.callback_data for b in buttons[1:]] == ["menu:order", "menu:profile"]
    assert "Анкета добровольная." in greeting.text
    await dispatcher.feed_raw_update(bot, {"update_id": 2, "callback_query": {
        "id": "consent-check", "from": user, "chat_instance": "preview", "data": "menu:order",
        "message": {"message_id": 2, "date": now, "chat": chat},
    }})
    assert any(b.callback_data == "pdn:agree" for row in session.messages[-1].reply_markup.inline_keyboard for b in row)
    assert await db.fetch_one("SELECT 1 FROM orders LIMIT 1") is None

    keyboard = "".join(
        f'<a href="{html.escape(b.url)}">{html.escape(b.text)}</a>' if b.url else
        f'<span>{html.escape(b.text)}</span>' for b in buttons
    )
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тест Telegram — Зооконсьерж</title><style>
body{{margin:0;background:#e8f0f6;color:#17212b;font:16px/1.5 system-ui}}
header{{background:white;padding:18px;font-weight:700}}
main{{margin:16px;max-width:420px}}small{{color:#485c6b}}
p{{background:white;border-radius:14px;padding:14px;white-space:pre-line}}
a,span{{display:block;padding:10px 12px;margin-top:5px;text-align:center;border-radius:8px;background:#fff;color:#075885}}
</style><header>Зооконсьерж</header><main><small>Локальный тест Telegram · /start</small>
<p>{html.escape(greeting.text)}</p>{keyboard}</main>"""


async def main():
    await db.init_db()
    session = TestTelegramSession()
    bot = Bot("1:test", session=webchat.WebRouterSession(session))
    greeting = await start_bot(bot, session)

    async def no_geocoding(query):
        return None

    geo.search = no_geocoding
    app = webserver.make_app()
    app["bot"], app["dp"] = bot, None

    async def greeting_page(request):
        return web.Response(text=greeting, content_type="text/html")

    app.router.add_get("/__preview/telegram", greeting_page)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", HOME_PORT).start()
    output = Path(os.environ.get("ZOOBOT_SCREENSHOTS", TEST_DB.name))
    output.mkdir(parents=True, exist_ok=True)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            mobile = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
            await mobile.route("**/*", lambda route: route.continue_() if route.request.url.startswith(BASE) else route.abort())
            page = await mobile.new_page()
            await page.goto(BASE)
            await page.evaluate("document.fonts.ready")
            await expect(page.get_by_text("Первый подбор — 0 ₽", exact=True)).to_be_visible()
            assert await page.evaluate("document.documentElement.scrollWidth === innerWidth")
            assert await page.locator(".hero-art img").evaluate("(img) => img.complete && img.naturalWidth > 0")
            survey = page.get_by_role("link", name="Открыть добровольную анкету")
            await expect(survey).to_have_attribute("href", SURVEY_URL)
            await expect(survey).to_contain_text("Пройди анкету — это сделает нас лучше")
            action = page.get_by_role("link", name="Подобрать исполнителя", exact=True)
            await expect(action).to_be_in_viewport()
            await page.screenshot(path=output / "zoobot-home-mobile.png")
            await action.click()
            await expect(page.get_by_text("Создайте профиль", exact=False)).to_be_visible()
            await page.locator("#service_id").select_option(value=await page.locator('option[data-cat="boarding"]').first.get_attribute("value"))
            assert await db.fetch_one("SELECT 1 FROM web_requests") is None
            assert not [c for c in await mobile.cookies() if c["name"] == webchat.COOKIE]
            await page.locator("#when").fill("С пятницы по понедельник")
            await page.locator("#address").fill("Москва, Тверская улица, 12")
            await page.locator("#pet").fill("Дымок, кот")
            await page.locator("#name").fill("Анна")
            await page.locator("#phone").fill("89162045831")
            await page.locator('input[name="consent"]').check()
            await page.get_by_role("button", name="Отправить заявку").click()
            await expect(page.get_by_role("heading", name="Заявка принята")).to_be_visible()
            req = await db.fetch_one("SELECT * FROM web_requests")
            user = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (req["web_uid"],))
            assert req["match_tier"] == "economy" and user["name"] == "Анна"
            assert [c for c in await mobile.cookies() if c["name"] == webchat.COOKIE]
            await page.get_by_role("link", name="Отправить ещё одну заявку").click()
            await page.locator("#service_id").select_option(str(req["service_id"]))
            for key, val in (("when", "На четыре дня"), ("address", "Москва, Тверская улица, 12"),
                             ("pet", "Дымок, кот"), ("name", "Анна"), ("phone", "89162045831")):
                await page.locator("#" + key).fill(val)
            await page.locator('input[name="consent"]').check()
            await page.get_by_role("button", name="Отправить заявку").click()
            await expect(page.get_by_role("heading", name="Заявка принята")).to_be_visible()
            later = await db.fetch_one("SELECT * FROM web_requests WHERE id<>?", (req["id"],))
            assert later["web_uid"] == req["web_uid"]

            admin = await browser.new_context()
            await admin.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": await webchat.new_session(111), "url": BASE}])
            panel = await admin.new_page()
            when = datetime.now(config.tz) + timedelta(days=4)
            for index, item in enumerate((later, req)):
                await panel.goto(f"{BASE}/portal/request/{item['id']}")
                await panel.locator("#start").fill(when.strftime("%Y-%m-%dT10:00"))
                await panel.locator("#qty").fill("4")
                await panel.locator("#executor_quote").fill("3200")
                await panel.get_by_role("button", name="Оформить заказ", exact=True).click()
                await panel.get_by_text("Заказ оформлен:", exact=False).wait_for()
                await panel.locator('a[href^="/portal/order/"]').first.click()
                order = await db.fetch_one("SELECT order_id FROM web_requests WHERE id=?", (item["id"],))
                oid = order["order_id"]
                expected = 0 if index == 0 else 99
                await expect(panel.get_by_text(f"Подбор · Эконом: {expected} ₽", exact=False)).to_be_visible()
                assert (await orders.get_order_full(oid))["status"] == "created"
                quote = await db.fetch_one("SELECT payload FROM web_events WHERE uid=? ORDER BY id DESC LIMIT 1",
                                           (item["web_uid"],))
                assert f"Подбор: {expected} ₽. Итого: {3200 + expected:,} ₽".replace(",", " ") in json.loads(quote["payload"])["text"]
                await panel.get_by_role("button", name="✅ Подтвердить", exact=True).click()
                await expect(panel.locator(".stbar")).to_contain_text("Подтверждён")
                saved = await orders.get_order_full(oid)
                payment = await db.fetch_one("SELECT amount FROM payments WHERE order_id=?", (oid,))
                assert (saved["quantity"], saved["match_fee"], saved["price_total"], payment["amount"]) == (4, expected, 3200 + expected, 3200 + expected)
            await page.goto(f"{BASE}/__preview/telegram")
            await expect(page.get_by_role("link", name="Заполнить анкету")).to_have_attribute("href", SURVEY_URL)
            await page.screenshot(path=output / "zoobot-telegram-start.png")
            await browser.close()
        print("PASS: мобильный первый экран; заявка и профиль; первый и следующий Эконом на четыре дня; /start и действующее меню")
        if os.environ.get("ZOOBOT_KEEP_PREVIEW") == "1":
            print(f"Предпросмотр: {BASE}", flush=True)
            await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await db.close_shared()


if __name__ == "__main__":
    asyncio.run(main())
