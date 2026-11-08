"""Обход сайта в настоящем Chromium: администратор и клиент проходят по всем ссылкам, какие видны на страницах.
Проверяет: нет ответов 4xx/5xx, нет ошибок JS, на каждой внутренней странице есть «Назад», из портала виден выход на главную.
Запуск: BOT_TOKEN=1:x python3 tests/e2e_crawl.py (нужен Playwright)"""
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_crawl.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
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

FAILS = []
SKIP = ("/portal/logout", "/chat/logout", "/healthz", "t.me", ".csv", "/export/")  # файлы для скачивания, не страницы
NO_BACK_OK = {"/", "/portal"}


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...
    async def make_request(self, bot, method, timeout=None):
        return True


async def seed():
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    u = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (4242001, 'Ира Тест', '+79001112233', 1)")
    o = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total) VALUES (?,?, '2030-01-01 10:00:00', 900)", (u, sid))
    await db.execute("INSERT INTO payments (order_id, amount) VALUES (?, 900)", (o,))
    await db.execute("INSERT INTO web_requests (service_id, name, phone, address) VALUES (?, 'Форма Фёдор', '+79007778899', 'Мира 1')", (sid,))
    await db.execute("INSERT INTO executors (tg_id, name, legal_status, services) VALUES (4242002, 'Олег', 'individual', 'walk')")


async def crawl(ctx, base, start, label, errors):
    page = await ctx.new_page()
    page.on("pageerror", lambda e: errors.append(f"{label}: JS {e}"))
    page.on("console", lambda m: errors.append(f"{label}: console {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
    seen, todo, report = set(), [start], {}
    while todo and len(seen) < 60:
        path = todo.pop(0)
        if path in seen or any(s in path for s in SKIP):
            continue
        seen.add(path)
        resp = await page.goto(base + path, wait_until="domcontentloaded")
        await page.wait_for_timeout(250)
        status = resp.status if resp else 0
        has_back = await page.locator("a.back").count() > 0
        home = await page.locator('a[href="/"]').count()
        report[path] = (status, has_back, home)
        for href in await page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
            u = urlparse(urljoin(base + path, href))
            if u.netloc == urlparse(base).netloc and not u.path.startswith(("/vendor", "/fonts", "/icons")):
                todo.append(u.path + (("?" + u.query) if u.query and "m=" not in u.query else ""))
    await page.close()
    return report


async def main():
    await db.init_db()
    await seed()
    bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
    app = webserver.make_app()
    app["bot"], app["dp"] = bot, build_dispatcher()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    errors = []
    async with async_playwright() as p:
        br = await p.chromium.launch()
        admin = await br.new_context(viewport={"width": 390, "height": 800})
        await admin.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": await webchat.new_session(111), "url": base}])
        rep = await crawl(admin, base, "/portal", "админ", errors)
        print(f"\nАдминистратор: пройдено страниц {len(rep)}")
        for path, (st, back, home) in sorted(rep.items()):
            check(st < 400, f"{path}: ответ {st}")
            if path.split("?")[0] not in NO_BACK_OK and not path.startswith("/portal/api"):
                check(back, f"{path}: есть «Назад»")
            if path.startswith("/portal") and not path.startswith("/portal/api") and not path.startswith("/portal/logout"):
                check(home > 0, f"{path}: есть выход на главную")
        client = await br.new_context(viewport={"width": 390, "height": 800})
        rep = await crawl(client, base, "/", "клиент", errors)
        print(f"\nКлиент: пройдено страниц {len(rep)}")
        for path, (st, back, home) in sorted(rep.items()):
            check(st < 500, f"{path}: ответ {st}")
            if path.split("?")[0] not in NO_BACK_OK:
                check(back, f"{path}: есть «Назад»")
        check(not errors, "без ошибок JS: " + "; ".join(errors[:5]))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {len(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
