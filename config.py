"""Конфигурация из .env. Токены и ID админов в коде не хранятся."""
import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _admin_ids() -> set[int]:
    raw = os.getenv("ADMIN_IDS", "")
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("BOT_TOKEN", "")
    admin_ids: frozenset = frozenset(_admin_ids())
    db_path: str = os.getenv("DB_PATH", "zoobot.db")
    policy_url: str = os.getenv("POLICY_URL", "https://example.com/privacy")
    tz: ZoneInfo = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))


config = Config()

if not config.bot_token:
    raise RuntimeError("Не задан BOT_TOKEN в .env")
