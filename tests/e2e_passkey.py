"""Вход по Face ID в настоящем Chromium с виртуальным устройством (WebAuthn): включение ключа в «Вход и почта»,
вход по ключу без cookie, отзыв ключа. Подпись проверяет сервер (passkeys.py) — тот же код, что на Railway."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_passkey.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp import web  # noqa: E402
from playwright.async_api import async_playwright  # noqa: E402

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

FAILS = []


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


async def main():
    await db.init_db()
    app = webserver.make_app()
    app["bot"] = None
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://localhost:{port}"  # WebAuthn не работает с IP-адресом вместо домена
    errors = []
    async with async_playwright() as p:
        br = await p.chromium.launch(channel="chromium")
        ctx = await br.new_context(viewport={"width": 390, "height": 780})
        token = await webchat.new_session(111, device="iPhone · Safari")
        await ctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": token, "url": base}])
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        cdp = await ctx.new_cdp_session(page)
        await cdp.send("WebAuthn.enable")
        await cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
            "protocol": "ctap2", "transport": "internal", "hasResidentKey": True, "hasUserVerification": True,
            "isUserVerified": True, "automaticPresenceSimulation": True}})

        await page.goto(base + "/portal/settings")
        check(await page.is_visible("#pkon"), "в «Вход и почта» есть кнопка «Включить вход по Face ID»")
        await page.click("#pkon")
        await page.wait_for_function("document.querySelectorAll('input[value=pk_revoke]').length === 1", timeout=15000)
        row = await db.fetch_one("SELECT uid, device FROM passkeys")
        check(row is not None and row["uid"] == 111, f"ключ создан и принят сервером: {row and dict(row)}")
        check(await page.evaluate("localStorage.getItem('zc_passkey')") == "1", "устройство запомнило, что ключ создан")

        # «в другой раз»: cookie нет — вход по Face ID
        await ctx.clear_cookies()
        await page.goto(base + "/portal")
        check("Вход в портал" in await page.inner_text("body"), "без cookie портал просит войти")
        check(await page.is_visible("#pklogin"), "на странице входа есть кнопка «Войти по Face ID»")
        await page.click("#pklogin")
        await page.wait_for_url(base + "/portal", timeout=15000)
        await page.wait_for_selector("text=Заявки", timeout=8000)
        check("Вход в портал" not in await page.inner_text("body"), "после Face ID открылся портал администратора")
        n = (await db.fetch_one("SELECT COUNT(*) AS n FROM web_sessions WHERE uid=111"))["n"]
        check(n == 2, f"выдана новая сессия администратора (всего {n})")
        used = await db.fetch_one("SELECT last_used FROM passkeys")
        check(bool(used["last_used"]), "время последнего входа по ключу записано")

        # отзыв ключа → вход по Face ID больше не работает
        await page.goto(base + "/portal/settings")
        await page.click("input[value=pk_revoke] ~ button")
        await page.wait_for_selector("text=Ключ Face ID отозван")
        check((await db.fetch_one("SELECT COUNT(*) AS n FROM passkeys"))["n"] == 0, "ключ отозван")
        await ctx.clear_cookies()
        await page.goto(base + "/portal")
        await page.click("#pklogin")
        await page.wait_for_function("document.getElementById('pkst').textContent.length > 5", timeout=15000)
        check("Вход в портал" in await page.inner_text("body"), "по отозванному ключу войти нельзя")
        check(not errors, "без ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
