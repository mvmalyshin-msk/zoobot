"""Разовая чистка тестовых данных (заявка владельца 8 ноября 2026, 18:40 по Москве): всё, что было создано до этой минуты, —
заказы, заявки с сайта, клиенты, питомцы, переписка — удаляется один раз. Настройки, услуги, цены, исполнители, администраторы
и входы в портал остаются.

Порядок: 1) копия всей базы файлом рядом с базой и Excel-выгрузка админу; 2) только если копия получилась — удаление в одной
транзакции; 3) отметка в kv, чтобы чистка не повторилась при следующем запуске. Не получилась копия — не удаляем ничего."""
import asyncio
import logging
import os
import sqlite3
from pathlib import Path

import db
from config import config

log = logging.getLogger(__name__)

CUTOFF_UTC = "2026-11-08 15:40:00"  # 18:40 по Москве
KV_KEY = "purge:2026-11-08"
RESET_COUNTERS = ("orders", "web_requests", "order_statuses_log", "payments", "reviews", "reminders", "pets", "users",
                  "messages", "executor_payouts", "consents")


def _backup_sync(dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(config.db_path)
    try:
        out = sqlite3.connect(dest)
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()


async def _delete_all(cutoff: str) -> dict:
    admins = tuple(config.admin_ids) or (0,)
    mark = ",".join("?" * len(admins))
    async with db.transaction() as conn:
        async def n(sql, params=()):
            return (await (await conn.execute(sql, params)).fetchone())[0]

        old_orders = "SELECT id FROM orders WHERE created_at <= ?"
        counts = {"orders": await n("SELECT COUNT(*) FROM orders WHERE created_at <= ?", (cutoff,))}
        for table in ("order_statuses_log", "payments", "executor_payouts", "reviews", "reminders"):
            await conn.execute(f"DELETE FROM {table} WHERE order_id IN ({old_orders})", (cutoff,))
        await conn.execute(f"DELETE FROM rooms WHERE order_id IN ({old_orders})", (cutoff,))
        await conn.execute("DELETE FROM orders WHERE created_at <= ?", (cutoff,))

        counts["requests"] = await n("SELECT COUNT(*) FROM web_requests WHERE created_at <= ?", (cutoff,))
        await conn.execute("DELETE FROM web_requests WHERE created_at <= ?", (cutoff,))

        # клиенты: созданные до отметки, не администраторы, не исполнители и без заказов, оставшихся после неё
        who = (f"SELECT id FROM users WHERE created_at <= ? AND tg_id NOT IN ({mark}) "
               "AND tg_id NOT IN (SELECT tg_id FROM executors) AND id NOT IN (SELECT user_id FROM orders)")
        params = (cutoff, *admins)
        counts["clients"] = await n(f"SELECT COUNT(*) FROM ({who})", params)
        await conn.execute(f"DELETE FROM pets WHERE user_id IN ({who})", params)
        await conn.execute(f"DELETE FROM consents WHERE user_id IN ({who})", params)
        await conn.execute(f"DELETE FROM reviews WHERE user_id IN ({who})", params)
        await conn.execute(f"DELETE FROM users WHERE id IN ({who})", params)

        await conn.execute("DELETE FROM messages WHERE created_at <= ?", (cutoff,))
        await conn.execute("DELETE FROM web_events WHERE created_at <= ?", (cutoff,))
        await conn.execute("DELETE FROM web_files WHERE created_at <= ?", (cutoff,))
        await conn.execute("DELETE FROM fsm")  # незаконченные диалоги тестов
        await conn.execute(f"DELETE FROM web_sessions WHERE created_at <= ? AND uid NOT IN ({mark})", (cutoff, *admins))

        for table in RESET_COUNTERS:  # нумерация с единицы, если в таблице ничего не осталось
            if await n(f"SELECT COUNT(*) FROM {table}") == 0:
                await conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (table,))
        await conn.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (KV_KEY, f"done {counts}"))
    return counts


async def run_once(bot=None, cutoff: str = CUTOFF_UTC, backup_dir: Path | None = None) -> dict | None:
    """Возвращает число удалённого или None, если чистка уже была или копия не получилась (тогда ничего не удалено)."""
    if await db.fetch_one("SELECT 1 AS x FROM kv WHERE key=?", (KV_KEY,)):
        return None
    folder = backup_dir or Path(os.path.dirname(os.path.abspath(config.db_path))) / "backups"
    dest = folder / "before-purge-2026-11-08.db"
    try:
        await asyncio.to_thread(_backup_sync, dest)
    except Exception:
        log.exception("Чистка отменена: не получилось сохранить копию базы")
        return None
    excel = None
    try:
        import exports
        excel = await exports.build_database_xlsx()
    except Exception:
        log.exception("Excel-копия перед чисткой не собралась (файл копии базы при этом есть)")
    counts = await _delete_all(cutoff)
    log.info("Чистка тестовых данных выполнена: %s; копия: %s", counts, dest)
    if bot is not None:
        from aiogram.types import BufferedInputFile
        text = (f"🧹 Тестовые данные до 18:40 8 ноября убраны: заказов {counts['orders']}, заявок с сайта {counts['requests']}, "
                f"клиентов {counts['clients']}. Настройки, цены и исполнители на месте. Копия всей базы сохранена на сервере: {dest}.")
        for admin_id in config.admin_ids:
            try:
                await bot.send_message(admin_id, text)
                if excel:
                    await bot.send_document(admin_id, BufferedInputFile(excel, filename="kopiya_pered_chistkoy.xlsx"),
                                            caption="Excel-копия того, что было до чистки.")
            except Exception:
                log.exception("Не удалось написать админу %s про чистку", admin_id)
    return counts
