"""Маленький веб-сервер внутри бота: отдаёт страницу карты (Telegram Mini App) для выбора адреса.

Страница статическая и никаких данных клиентов не хранит: результат уходит обратно в бота через Telegram
(WebApp.sendData). Railway сам выдаёт порт (переменная PORT) и публичный домен.
"""
import logging
import os
from pathlib import Path

from aiohttp import web

import cabinet
from config import config
import policy
import portal
import seo
import webchat
import webform
import contact
import siteleads

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"


async def _map(request: web.Request) -> web.Response:
    # страница карты (мини-приложение выбора адреса); no-cache: после обновления бота клиенты сразу видят новую страницу
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def _index(request: web.Request) -> web.Response:
    """Главная: описание сервиса и три входа для заявки. Лёгкая страница без внешних скриптов."""
    page = ((WEBAPP_DIR / "home.html").read_text(encoding="utf-8").replace("__BOT__", config.bot_username)
            .replace("__DESC__", seo.DESCRIPTION).replace("__SEO__", seo.head_tags(seo.base_url(request)))
            .replace("__FAQ__", seo.faq_html()))
    return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _policy(request: web.Request) -> web.Response:
    html = policy.render_html(policy.operator_from_env(os.environ))
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


async def _brand_css(request: web.Request) -> web.Response:
    return web.FileResponse(WEBAPP_DIR / "brand.css", headers={"Cache-Control": "no-cache", "Content-Type": "text/css"})


async def _sw(request: web.Request) -> web.Response:
    """Служебный скрипт уведомлений: с корня сайта, чтобы работать и для /chat, и для /portal/bot."""
    return web.FileResponse(WEBAPP_DIR / "sw.js", headers={"Cache-Control": "no-cache",
                                                          "Content-Type": "application/javascript; charset=utf-8"})


async def _manifest_admin(request: web.Request) -> web.Response:
    """Приложение администратора на экране «Домой»: открывает портал."""
    return web.FileResponse(WEBAPP_DIR / "manifest-admin.webmanifest",
                            headers={"Cache-Control": "no-cache", "Content-Type": "application/manifest+json"})


async def _manifest(request: web.Request) -> web.Response:
    return web.FileResponse(WEBAPP_DIR / "manifest.webmanifest",
                            headers={"Cache-Control": "no-cache", "Content-Type": "application/manifest+json"})


async def _touch_icon(request: web.Request) -> web.Response:
    return web.FileResponse(WEBAPP_DIR / "icons" / "apple-touch-icon.png")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/map", _map)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/policy", _policy)
    app.router.add_get("/request", webform.get_form)
    app.router.add_post("/request", webform.post_form)
    app.router.add_get("/contact", contact.get_page)
    app.router.add_post("/contact", contact.post_page)
    app.router.add_get("/contact/call", contact.call)
    app.router.add_post("/api/site-lead", siteleads.post_lead)
    webchat.setup(app)
    cabinet.setup(app)
    portal.setup(app)
    seo.setup(app)
    import servicepages
    servicepages.setup(app)
    import articles
    articles.setup(app)
    import analytics
    analytics.setup(app)
    import phoneverify
    phoneverify.setup(app)
    app.router.add_static("/vendor/", WEBAPP_DIR / "vendor", follow_symlinks=False)
    app.router.add_static("/fonts/", WEBAPP_DIR / "fonts", follow_symlinks=False)
    app.router.add_static("/icons/", WEBAPP_DIR / "icons", follow_symlinks=False)
    app.router.add_get("/brand.css", _brand_css)
    app.router.add_get("/manifest.webmanifest", _manifest)
    app.router.add_get("/sw.js", _sw)
    app.router.add_get("/manifest-admin.webmanifest", _manifest_admin)
    app.router.add_get("/apple-touch-icon.png", _touch_icon)  # iOS ищет иконку и по этому адресу  # add_static не выходит из папки
    return app


async def start(port: int, bot=None, dp=None) -> web.AppRunner:
    app = make_app()
    app["dp"] = dp
    app["bot"] = bot  # веб-заявки уведомляют админа через бота
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Веб-сервер карты запущен на порту %s", port)
    return runner
