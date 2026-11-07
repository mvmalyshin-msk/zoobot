"""Маленький веб-сервер внутри бота: отдаёт страницу карты (Telegram Mini App) для выбора адреса.

Страница статическая и никаких данных клиентов не хранит: результат уходит обратно в бота через Telegram
(WebApp.sendData). Railway сам выдаёт порт (переменная PORT) и публичный домен.
"""
import logging
import os
from pathlib import Path

from aiohttp import web

import policy
import webform

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).parent / "webapp"


async def _index(request: web.Request) -> web.Response:
    # no-cache: после обновления бота клиенты сразу видят новую страницу
    return web.FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _policy(request: web.Request) -> web.Response:
    html = policy.render_html(policy.operator_from_env(os.environ))
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache"})


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _index)
    app.router.add_get("/healthz", _health)
    app.router.add_get("/policy", _policy)
    app.router.add_get("/request", webform.get_form)
    app.router.add_post("/request", webform.post_form)
    app.router.add_static("/vendor/", WEBAPP_DIR / "vendor", follow_symlinks=False)  # add_static не выходит из папки
    return app


async def start(port: int, bot=None) -> web.AppRunner:
    app = make_app()
    app["bot"] = bot  # веб-заявки уведомляют админа через бота
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    log.info("Веб-сервер карты запущен на порту %s", port)
    return runner
