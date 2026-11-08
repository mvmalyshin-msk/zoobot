"""Прокси для связи с Telegram. Если бот стоит на российском сервере, Telegram оттуда напрямую не открывается:
тогда в переменной TELEGRAM_PROXY задаётся адрес прокси (socks5://пользователь:пароль@адрес:порт или http://…).
Пароль прокси лежит только в переменных сервера и никогда не попадает в журнал."""
import os
from urllib.parse import urlparse

from aiogram.client.session.aiohttp import AiohttpSession


def proxy_url() -> str | None:
    return (os.environ.get("TELEGRAM_PROXY") or "").strip() or None


def make_session() -> AiohttpSession:
    return AiohttpSession(proxy=proxy_url())


def describe() -> str:
    """Строка для журнала: куда идёт связь с Telegram, без логина и пароля."""
    url = proxy_url()
    if not url:
        return "Telegram: напрямую"
    u = urlparse(url)
    return f"Telegram: через прокси {u.scheme}://{u.hostname}:{u.port}"
