"""Уведомления (Web Push) в настоящем Chromium: кнопка «🔔 Уведомления» подписывает браузер на свой адрес API,
служебный скрипт регистрируется и показывает уведомление, когда браузеру приходит push.
Связь с серверами Google из песочницы недоступна, поэтому подменяется только pushManager.subscribe."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_e2e_push.db"
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


def check(ok, name):
    print(("  ✅ " if ok else "  ❌ ") + name)
    if not ok:
        FAILS.append(name)


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...
    async def make_request(self, bot, method, timeout=None):
        return True


FAKE_PUSH = """
(() => {
  const keys = {p256dh: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
                auth: 'BTBZMqHH6r4Tts7J_aSIgg'};
  let sub = null;
  PushManager.prototype.getSubscription = function () { return Promise.resolve(sub); };
  PushManager.prototype.subscribe = function (opts) {
    window.__appKeyLen = opts && opts.applicationServerKey ? opts.applicationServerKey.byteLength : 0;
    sub = {toJSON: () => ({endpoint: 'https://fcm.googleapis.com/fcm/send/' + ENDPOINT, keys})};
    return Promise.resolve(sub);
  };
})();
"""


async def main():
    await db.init_db()
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
        br = await p.chromium.launch(channel="chromium")  # полноценный Chromium: в облегчённом уведомления всегда «запрещены»

        # --- клиент: включает уведомления в /chat
        ctx = await br.new_context(viewport={"width": 390, "height": 780})
        await ctx.grant_permissions(["notifications"], origin=base)
        await ctx.add_init_script(FAKE_PUSH.replace("ENDPOINT", "'client'"))
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(base + "/chat")
        await page.wait_for_selector(".m.bot", timeout=8000)
        await page.wait_for_timeout(1500)  # разрешение уже дано — подписка молча
        uid = (await db.fetch_one("SELECT uid FROM web_sessions ORDER BY id DESC LIMIT 1"))["uid"]
        row = await db.fetch_one("SELECT uid, endpoint FROM push_subs WHERE endpoint LIKE '%/client'")
        check(row is not None and row["uid"] == uid, f"клиент подписан на уведомления своей сессией: {row and dict(row)}")
        check(await page.evaluate("window.__appKeyLen") == 65, "браузеру передан ключ сервера (65 байт)")
        check(await page.evaluate("document.getElementById('nt').hidden"), "кнопка «🔔» скрыта, когда уведомления включены")

        # служебный скрипт зарегистрирован и показывает уведомление на push
        sw = await page.evaluate("navigator.serviceWorker.ready.then(r => r.active && r.active.scriptURL)")
        check(sw == base + "/sw.js", f"служебный скрипт работает: {sw}")
        cdp = await ctx.new_cdp_session(page)
        regs = []
        cdp.on("ServiceWorker.workerRegistrationUpdated", lambda e: regs.extend(e["registrations"]))
        await cdp.send("ServiceWorker.enable")
        await page.wait_for_timeout(500)
        reg_id = next((r["registrationId"] for r in regs if r["scopeURL"].startswith(base)), None)
        data = json.dumps({"title": "Зооконсьерж", "body": "👤 Администратор: добрый день", "url": "/chat"})
        await cdp.send("ServiceWorker.deliverPushMessage", {"origin": base, "registrationId": reg_id, "data": data})
        await page.wait_for_timeout(800)
        shown = await page.evaluate("navigator.serviceWorker.ready.then(r => r.getNotifications())"
                                    ".then(ns => ns.map(n => [n.title, n.body]))")
        check(["Зооконсьерж", "👤 Администратор: добрый день"] in shown, f"уведомление показано: {shown}")

        # --- администратор: включает уведомления в портале (/portal/bot), не в клиентском чате
        token = await webchat.new_session(111)
        actx = await br.new_context(viewport={"width": 390, "height": 780})
        await actx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": token, "url": base}])
        await actx.add_init_script(FAKE_PUSH.replace("ENDPOINT", "'admin'") +
                                   "Object.defineProperty(Notification, 'permission', {get: () => window.__perm || 'default'});"
                                   "Notification.requestPermission = () => { window.__perm = 'granted';"
                                   " return Promise.resolve('granted'); };")
        await actx.grant_permissions(["notifications"], origin=base)
        ap = await actx.new_page()
        ap.on("pageerror", lambda e: errors.append(str(e)))
        await ap.goto(base + "/portal/bot")
        await ap.wait_for_timeout(1200)
        check(not await ap.evaluate("document.getElementById('nt').hidden"), "в портале видна кнопка «🔔 Уведомления»")
        await ap.click("#nt")
        await ap.wait_for_timeout(1200)
        row = await db.fetch_one("SELECT uid FROM push_subs WHERE endpoint LIKE '%/admin'")
        check(row is not None and row["uid"] == 111, "админ подписан на уведомления в портале своей учётной записью")
        check("Уведомления включены" in await ap.inner_text("#st"), "админу сказано: уведомления включены")
        # --- приложение «ЗК Админ» с экрана «Домой»: своё хранилище, стартовая страница /portal (не /portal/bot)
        pctx = await br.new_context(viewport={"width": 390, "height": 780})
        await pctx.add_cookies([{"name": webchat.ADMIN_COOKIE, "value": token, "url": base}])
        await pctx.add_init_script(FAKE_PUSH.replace("ENDPOINT", "'pwa'") +
                                   "Object.defineProperty(Notification, 'permission', {get: () => window.__perm || 'default'});"
                                   "Notification.requestPermission = () => { window.__perm = 'granted';"
                                   " return Promise.resolve('granted'); };")
        await pctx.grant_permissions(["notifications"], origin=base)
        pp = await pctx.new_page()
        pp.on("pageerror", lambda e: errors.append(str(e)))
        await pp.goto(base + "/portal")
        await pp.wait_for_timeout(800)
        check(not await pp.evaluate("document.getElementById('perm').hidden"), "на стартовой странице /portal видна кнопка «🔔 Уведомления»")
        await pp.click("#perm")
        await pp.wait_for_timeout(1200)
        row = await db.fetch_one("SELECT uid FROM push_subs WHERE endpoint LIKE '%/pwa'")
        check(row is not None and row["uid"] == 111, "приложение с экрана «Домой» подписано прямо со страницы /portal")
        check(await pp.evaluate("document.getElementById('perm').hidden"), "после включения кнопка скрыта")
        await pp.goto(base + "/portal/settings")
        await pp.wait_for_timeout(500)
        check("Устройств с уведомлениями: 2" in await pp.inner_text("main"), "в «Вход и почта» видно 2 устройства")
        await pp.click("#pushtest")
        await pp.wait_for_function("document.getElementById('pushst').textContent.length > 12 && !document.getElementById('pushst').textContent.startsWith('Отправляю')", timeout=20000)
        check(len(await pp.inner_text("#pushst")) > 12, "кнопка «Проверить уведомления» показывает результат: " + (await pp.inner_text("#pushst"))[:60])
        check(not errors, "без ошибок JS: " + "; ".join(errors))
        await br.close()
    await runner.cleanup()
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
