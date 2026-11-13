"""Конфигурация из .env. Токены и ID админов в коде не хранятся."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    ids = [int(x) for x in raw.replace(" ", "").split(",") if x]
    return set(ids[:1])  # администратор один — первый в списке; остальные id не получают доступа


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: frozenset = frozenset(_admin_ids())
    db_path: str = os.getenv("DB_PATH", "zoobot.db")
    policy_url: str = os.getenv("POLICY_URL", "https://example.com/privacy")
    # Публичный адрес сайта бота (страница карты).
    webapp_url: str = os.getenv("WEBAPP_URL") or ""
    tz: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
    # имя бота для ссылок t.me/<имя>?start=… (вход админа, подтверждение веб-клиента через Telegram)
    bot_username: str = os.getenv("BOT_USERNAME", "zooconcierge_bot")
    # Яндекс ID для подтверждения телефона (см. phoneverify.py); без них кнопка не показывается
    yandex_client_id: str = os.getenv("YANDEX_CLIENT_ID", "")
    yandex_client_secret: str = os.getenv("YANDEX_CLIENT_SECRET", "")


config = Config()

if not config.bot_token:
    raise RuntimeError("Не задан BOT_TOKEN в .env")
