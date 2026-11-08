"""Напоминания на APScheduler.

Сами напоминания лежат в таблице reminders (orders.schedule_reminder), поэтому переживают
перезапуск бота. Планировщик раз в минуту берёт «созревшие» и отправляет.
"""
import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import analytics
import report
import texts
from config import config
from orders import get_order_full, notify
from utils import fmt_local

log = logging.getLogger(__name__)


def _time_only(value: str) -> str:
    return fmt_local(value).split(" ")[1]


async def _send_one(bot: Bot, r: dict) -> None:
    """Отправляет одно напоминание, если оно ещё актуально."""
    o = await get_order_full(r["order_id"])
    if o is None or o["status"] in ("cancelled", "completed"):
        return
    kind = r["kind"]

    if kind in ("client_24h", "client_2h") and o["status"] in ("paid", "assigned"):
        tpl = texts.REMIND_CLIENT_24H if kind == "client_24h" else texts.REMIND_CLIENT_2H
        await notify(bot, o["client_tg_id"], tpl.format(
            time=_time_only(o["start_at"]), service=o["service_name"], id=o["id"]))

    elif kind == "executor_2h" and o["status"] == "assigned" and o["executor_tg_id"]:
        await notify(bot, o["executor_tg_id"], texts.REMIND_EXECUTOR_2H.format(
            id=o["id"], service=o["service_name"], time=_time_only(o["start_at"]),
            address=o["address"]))

    elif kind == "pay_3h" and o["status"] == "confirmed":
        pay = await db.fetch_one(
            "SELECT payment_link FROM payments WHERE order_id=? AND status='link_sent'", (o["id"],))
        if pay:  # если уже оплачено — платёж не в link_sent, напоминать не нужно
            await notify(bot, o["client_tg_id"],
                         texts.REMIND_PAY.format(id=o["id"], link=pay["payment_link"]))


async def process_due_reminders(bot: Bot) -> int:
    """Обрабатывает все созревшие напоминания. Возвращает их число (удобно для тестов)."""
    due = await db.fetch_all(
        "SELECT * FROM reminders WHERE sent=0 AND send_at <= datetime('now') ORDER BY send_at")
    for r in due:
        try:
            await _send_one(bot, r)
        except Exception:
            log.exception("Ошибка напоминания %s", r["id"])
        # помечаем отправленным в любом случае, чтобы не слать повторно каждую минуту
        await db.execute(f"UPDATE reminders SET sent=1, {db.touch()} WHERE id=?", (r["id"],))
    return len(due)


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(process_due_reminders, "interval", seconds=60, args=[bot],
                      id="reminders", max_instances=1, coalesce=True)
    # отчёт в Excel в конце дня по Москве (пустой день пропускается)
    scheduler.add_job(report.send_daily_report, "cron", hour=23, minute=55, timezone=config.tz, args=[bot],
                      id="daily_report", max_instances=1, coalesce=True, misfire_grace_time=600)
    scheduler.add_job(analytics.cleanup, "cron", hour=3, minute=20, timezone=config.tz,
                      id="analytics_cleanup", max_instances=1, coalesce=True)  # журнал аналитики старше 2 лет
    return scheduler
