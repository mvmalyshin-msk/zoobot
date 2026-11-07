"""Мелкие общие функции: время, деньги, разбор даты из текста клиента.

Правило: в БД время хранится в UTC строкой 'YYYY-MM-DD HH:MM:SS',
клиенту и админу показываем по Москве (config.tz).
"""
import re
from datetime import datetime, timedelta, timezone

from config import config

UTC = timezone.utc
DB_FMT = "%Y-%m-%d %H:%M:%S"


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_db(dt: datetime) -> str:
    """aware-datetime → строка для БД (UTC)."""
    return dt.astimezone(UTC).strftime(DB_FMT)


def from_db(s: str) -> datetime:
    """Строка из БД → aware-datetime в UTC."""
    return datetime.strptime(s, DB_FMT).replace(tzinfo=UTC)


def to_local(dt: datetime) -> datetime:
    return dt.astimezone(config.tz)


def fmt_local(value) -> str:
    """Строка из БД или datetime → '08.11.2026 18:30' по Москве."""
    if isinstance(value, str):
        value = from_db(value)
    return to_local(value).strftime("%d.%m.%Y %H:%M")


def rub(n) -> str:
    """12500 → '12 500 ₽'."""
    return f"{int(round(n)):,}".replace(",", " ") + " ₽"


_REL_DAYS = {"сегодня": 0, "завтра": 1, "послезавтра": 2}


def parse_user_datetime(text: str, now: datetime | None = None) -> datetime | None:
    """Понимает: 'завтра 18:30', 'сегодня в 9.00', '12.11 09:00', '12.11.2026 09:00'.
    Возвращает aware-datetime по Москве или None, если не разобрали."""
    now = now or datetime.now(config.tz)
    t = re.sub(r"\s+", " ", text.strip().lower().replace(",", " "))

    try:
        m = re.fullmatch(r"(сегодня|завтра|послезавтра)(?: в)? (\d{1,2})[:.\-](\d{2})", t)
        if m:
            day = now.date() + timedelta(days=_REL_DAYS[m.group(1)])
            return datetime(day.year, day.month, day.day, int(m.group(2)), int(m.group(3)), tzinfo=config.tz)

        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))? (?:в )?(\d{1,2})[:.\-](\d{2})", t)
        if not m:
            return None
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        h, mi = int(m.group(4)), int(m.group(5))
        if y:
            year = int(y) + (2000 if len(y) == 2 else 0)
        else:
            year = now.year
            # дата без года, уже прошедшая в этом году → имеем в виду следующий год
            if datetime(year, mo, d, tzinfo=config.tz).date() < now.date():
                year += 1
        return datetime(year, mo, d, h, mi, tzinfo=config.tz)
    except ValueError:  # 31.02, 25:00 и т.п.
        return None


def min_age(birth_year: int) -> int:
    """Минимально возможный возраст по одному году рождения (дня рождения не знаем, поэтому осторожно)."""
    return now_utc().year - birth_year - 1


def normalize_phone(raw: str) -> str | None:
    """Российский номер → '+7XXXXXXXXXX' или None. Очевидно выдуманный (одна цифра подряд, 0123456789…) — None."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        national = digits[1:]
    elif len(digits) == 10:
        national = digits
    else:
        return None
    if len(set(national)) == 1 or national in "01234567890123456789" or national in "98765432109876543210":
        return None
    return "+7" + national
