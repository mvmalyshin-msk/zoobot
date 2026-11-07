"""Ежедневный отчёт для админа: новые клиенты и заказы за день, файлом Excel.

Файл строится из базы заново при каждой отправке, отдельного накопителя нет — поэтому клиент, которого
удалили по его просьбе (repo.erase_user), в следующие отчёты не попадает. Уже отправленные файлы лежат
в чате админа: при удалении клиента их нужно подчистить самому.
"""
import io
import logging
from datetime import date, datetime, time, timedelta

import openpyxl
from aiogram import Bot
from aiogram.types import BufferedInputFile
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import db
import texts
from config import config
from utils import fmt_local, to_db

log = logging.getLogger(__name__)


def safe(value):
    """Значение в ячейку. Тексты от клиентов могут начинаться с «=», «+», «-», «@» — Excel примет их за формулу,
    поэтому такие строки экранируем апострофом. Пустое → «—»."""
    if value is None or value == "":
        return "—"
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _bounds(day: date) -> tuple[str, str]:
    """Сутки по местному времени → границы в UTC для запроса к базе."""
    start = datetime.combine(day, time(0, 0), tzinfo=config.tz)
    return to_db(start), to_db(start + timedelta(days=1))


def _sheet(wb, title: str, header: list[str], rows: list[list], first: bool):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([safe(v) for v in r])
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None] or [8])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 45)
    ws.freeze_panes = "A2"


async def build_daily_xlsx(day: date) -> tuple[bytes, int, int]:
    """Возвращает (файл, число новых клиентов, число заказов) за сутки `day` по местному времени."""
    lo, hi = _bounds(day)
    clients = await db.fetch_all(
        """SELECT u.created_at, u.name, u.phone, u.tg_username, u.district, u.address,
                  (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) AS n_orders
           FROM users u WHERE u.created_at >= ? AND u.created_at < ? ORDER BY u.created_at""", (lo, hi))
    orders = await db.fetch_all(
        """SELECT o.id, o.created_at, u.name, u.phone, s.name AS service, o.status, o.start_at, o.address, o.price_total
           FROM orders o JOIN users u ON u.id=o.user_id JOIN services s ON s.id=o.service_id
           WHERE o.created_at >= ? AND o.created_at < ? ORDER BY o.created_at""", (lo, hi))
    wb = openpyxl.Workbook()
    _sheet(wb, "Новые клиенты",
           ["Время", "Имя", "Телефон", "Telegram", "Район", "Адрес", "Заказов"],
           [[fmt_local(c["created_at"]), c["name"], c["phone"],
             f"@{c['tg_username']}" if c["tg_username"] else None, c["district"], c["address"], c["n_orders"]]
            for c in clients], first=True)
    _sheet(wb, "Заказы",
           ["№", "Создан", "Клиент", "Телефон", "Услуга", "Статус", "Дата услуги", "Адрес", "Сумма, ₽"],
           [[o["id"], fmt_local(o["created_at"]), o["name"], o["phone"], o["service"],
             texts.STATUS_LABELS.get(o["status"], o["status"]), fmt_local(o["start_at"]), o["address"],
             o["price_total"]] for o in orders], first=False)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), len(clients), len(orders)


async def send_daily_report(bot: Bot, day: date | None = None, *, always: bool = False) -> bool:
    """Шлёт файл всем админам. Пустой день по расписанию пропускаем (always=True — по запросу /report)."""
    day = day or datetime.now(config.tz).date()
    data, n_clients, n_orders = await build_daily_xlsx(day)
    if not (n_clients or n_orders or always):
        return False
    caption = f"Отчёт за {day:%d.%m.%Y}: новых клиентов {n_clients}, заказов {n_orders}."
    sent = False
    for admin_id in config.admin_ids:
        try:
            await bot.send_document(admin_id, BufferedInputFile(data, filename=f"otchet_{day:%Y-%m-%d}.xlsx"),
                                    caption=caption)
            sent = True
        except Exception:
            log.exception("Не удалось отправить отчёт админу %s (он нажал /start у бота?)", admin_id)
    return sent
