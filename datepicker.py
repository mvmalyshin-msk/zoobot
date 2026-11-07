"""Выбор даты и времени кнопками: месяц → час → минуты.

Всё состояние лежит в самих кнопках (callback_data), поэтому окно можно листать
и после перезапуска бота. Формат callback_data (≤ 64 байт):
  cal:m:2026-12              показать месяц
  cal:d:2026-12-05           выбран день → показать часы
  cal:h:2026-12-05:09        выбран час → показать минуты
  cal:t:2026-12-05:09:30     выбрано время целиком
  cal:x                      пустая кнопка (прошлое, заголовок)
Правила времени те же, что у ввода текстом: запас до начала (min_ahead: для такси 30 минут,
для остальных услуг сутки — нужно время найти исполнителя) и не дальше чем через 366 дней.
"""
import calendar
import re
from datetime import date, datetime, timedelta

from aiogram.types import InlineKeyboardButton as Btn, InlineKeyboardMarkup

from config import config

# Заказы принимаем на время с 06:00 до 22:00 включительно (решение владельца) — и кнопками, и текстом.
WORK_FROM = 6
WORK_TO = 22
MIN_AHEAD = timedelta(minutes=30)
MAX_AHEAD = timedelta(days=366)
QUARTERS = (0, 15, 30, 45)
MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
          "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]
WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_NOOP = "cal:x"


BLANK = "\u2800"  # невидимый символ: Telegram не принимает кнопку с пустым текстом


def _dead(text: str = BLANK) -> Btn:
    return Btn(text=text, callback_data=_NOOP)


def in_work_hours(dt: datetime) -> bool:
    """С WORK_FROM:00 до WORK_TO:00 включительно (в 22:00 — можно, в 22:15 — уже нет)."""
    sec = dt.hour * 3600 + dt.minute * 60 + dt.second
    return WORK_FROM * 3600 <= sec <= WORK_TO * 3600 and not (sec == WORK_TO * 3600 and dt.microsecond)


def first_allowed(dt: datetime) -> datetime:
    """Самое раннее время в рабочие часы не раньше dt: ночь переносится на утро."""
    if in_work_hours(dt):
        return dt
    morning = dt.replace(hour=WORK_FROM, minute=0, second=0, microsecond=0)
    return morning if dt < morning else morning + timedelta(days=1)


def _slot_ok(dt: datetime, now: datetime, min_ahead: timedelta = MIN_AHEAD) -> bool:
    return in_work_hours(dt) and now + min_ahead <= dt <= now + MAX_AHEAD


def allowed(dt: datetime, now: datetime, min_ahead: timedelta = MIN_AHEAD) -> bool:
    """То же правило, что у кнопок календаря: рабочие часы, запас до начала, не дальше года."""
    return _slot_ok(dt, now, min_ahead)


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=config.tz)


def _day_has_slots(day: date, now: datetime, min_ahead: timedelta) -> bool:
    return any(_slot_ok(_at(day, h, q), now, min_ahead) for h in range(24) for q in QUARTERS)


def _month_key(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


def month_markup(year: int, month: int, now: datetime,
                 min_ahead: timedelta = MIN_AHEAD) -> InlineKeyboardMarkup:
    """Календарь месяца. Дни, на которые уже нельзя записаться, — неактивные точки."""
    today = now.date()
    last_day = (now + MAX_AHEAD).date()
    prev_y, prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_y, next_m = (year + 1, 1) if month == 12 else (year, month + 1)

    can_prev = (year, month) > (today.year, today.month)
    can_next = date(next_y, next_m, 1) <= last_day
    rows = [[
        Btn(text="◀", callback_data=f"cal:m:{_month_key(prev_y, prev_m)}") if can_prev else _dead(),
        _dead(f"{MONTHS[month - 1]} {year}"),
        Btn(text="▶", callback_data=f"cal:m:{_month_key(next_y, next_m)}") if can_next else _dead(),
    ], [_dead(w) for w in WEEKDAYS]]

    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        row = []
        for d in week:
            if d == 0:
                row.append(_dead())
                continue
            day = date(year, month, d)
            if not _day_has_slots(day, now, min_ahead):
                row.append(_dead("·"))
            else:
                label = f"•{d}" if day == today else str(d)
                row.append(Btn(text=label, callback_data=f"cal:d:{day.isoformat()}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def hours_markup(day: date, now: datetime, min_ahead: timedelta = MIN_AHEAD) -> InlineKeyboardMarkup:
    """Часы выбранного дня (только те, где есть хоть один свободный слот)."""
    btns = [Btn(text=f"{h:02d}:00", callback_data=f"cal:h:{day.isoformat()}:{h:02d}")
            for h in range(24) if any(_slot_ok(_at(day, h, q), now, min_ahead) for q in QUARTERS)]
    rows = [btns[i:i + 4] for i in range(0, len(btns), 4)]
    rows.append([Btn(text="◀ Другая дата", callback_data=f"cal:m:{_month_key(day.year, day.month)}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def minutes_markup(day: date, hour: int, now: datetime,
                   min_ahead: timedelta = MIN_AHEAD) -> InlineKeyboardMarkup:
    btns = [Btn(text=f"{hour:02d}:{q:02d}", callback_data=f"cal:t:{day.isoformat()}:{hour:02d}:{q:02d}")
            for q in QUARTERS if _slot_ok(_at(day, hour, q), now, min_ahead)]
    return InlineKeyboardMarkup(inline_keyboard=[
        btns, [Btn(text="◀ Другой час", callback_data=f"cal:d:{day.isoformat()}")]])


_RE_M = re.compile(r"cal:m:(\d{4})-(\d{2})")
_RE_D = re.compile(r"cal:d:(\d{4})-(\d{2})-(\d{2})")
_RE_H = re.compile(r"cal:h:(\d{4})-(\d{2})-(\d{2}):(\d{2})")
_RE_T = re.compile(r"cal:t:(\d{4})-(\d{2})-(\d{2}):(\d{2}):(\d{2})")


def parse(data: str):
    """callback_data → (вид, значение, час) или None, если это не наша или битая кнопка.
    Виды: 'm' — первое число месяца, 'd' — день, 'h' — день и час, 't' — готовое время, 'x' — пусто."""
    try:
        if data == _NOOP:
            return ("x", None, None)
        if m := _RE_M.fullmatch(data):
            return ("m", date(int(m[1]), int(m[2]), 1), None)
        if m := _RE_D.fullmatch(data):
            return ("d", date(int(m[1]), int(m[2]), int(m[3])), None)
        if m := _RE_H.fullmatch(data):
            hour = int(m[4])
            if not 0 <= hour <= 23:
                return None
            return ("h", date(int(m[1]), int(m[2]), int(m[3])), hour)
        if m := _RE_T.fullmatch(data):
            hour, minute = int(m[4]), int(m[5])
            if not 0 <= hour <= 23 or minute not in QUARTERS:
                return None
            return ("t", _at(date(int(m[1]), int(m[2]), int(m[3])), hour, minute), None)
    except ValueError:
        return None
    return None
