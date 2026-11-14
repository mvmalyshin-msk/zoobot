"""Напоминания на APScheduler.

Сами напоминания лежат в таблице reminders (orders.schedule_reminder), поэтому переживают
перезапуск бота. Планировщик раз в минуту берёт «созревшие» и отправляет.
"""
import logging
from datetime import timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import db
import analytics
import report
import texts
from config import config
from orders import get_order_full, notify
from utils import fmt_local

log = logging.getLogger(__name__)

MAX_REMINDER_ATTEMPTS = 5
RETRY_DELAYS_MINUTES = (1, 5, 15, 60)


def _time_only(value: str) -> str:
    return fmt_local(value).split(" ")[1]


async def _send_one(bot: Bot, r: dict) -> tuple[bool, str | None]:
    """Возвращает (успешно отправлено, причина терминального пропуска)."""
    o = await get_order_full(r["order_id"])
    if o is None:
        return False, "order_missing"
    if o["status"] in ("cancelled", "completed"):
        return False, f"order_{o['status']}"

    kind = r["kind"]
    pay = None
    if kind == "pay_3h" and o["status"] == "confirmed":
        pay = await db.fetch_one(
            "SELECT payment_link FROM payments WHERE order_id=? AND status='link_sent'", (o["id"],))
        if not pay:
            return False, "not_applicable"
    elif kind in ("client_24h", "client_2h") and o["status"] in ("paid", "assigned"):
        pass
    elif kind == "executor_2h" and o["status"] == "assigned" and o["executor_tg_id"]:
        pass
    else:
        return False, "not_applicable"

    # Re-read immediately before delivery so a concurrent cancel/completion is not notified.
    o = await get_order_full(r["order_id"])
    if o is None:
        return False, "order_missing"
    if o["status"] in ("cancelled", "completed"):
        return False, f"order_{o['status']}"

    if kind in ("client_24h", "client_2h") and o["status"] in ("paid", "assigned"):
        tpl = texts.REMIND_CLIENT_24H if kind == "client_24h" else texts.REMIND_CLIENT_2H
        delivered = await notify(bot, o["client_tg_id"], tpl.format(
            time=_time_only(o["start_at"]), service=o["service_name"], id=o["id"]),
            raise_forbidden=True)
    elif kind == "executor_2h" and o["status"] == "assigned" and o["executor_tg_id"]:
        delivered = await notify(bot, o["executor_tg_id"], texts.REMIND_EXECUTOR_2H.format(
            id=o["id"], service=o["service_name"], time=_time_only(o["start_at"]),
            address=o["address"]), raise_forbidden=True)
    elif kind == "pay_3h" and o["status"] == "confirmed" and pay:
        delivered = await notify(bot, o["client_tg_id"],
                                 texts.REMIND_PAY.format(id=o["id"], link=pay["payment_link"]),
                                 raise_forbidden=True)
    else:
        return False, "not_applicable"
    return delivered, None


async def _claim_reminder(reminder_id: int) -> int | None:
    """Persist an attempt before calling Telegram, including across process restarts."""
    async with db.transaction() as conn:
        cur = await conn.execute(
            """UPDATE reminders SET attempts=attempts+1, updated_at=datetime('now')
               WHERE id=? AND sent=0 AND terminal_reason IS NULL
                 AND send_at <= datetime('now') AND attempts < ?
                 AND EXISTS (SELECT 1 FROM orders o
                             WHERE o.id=reminders.order_id
                               AND o.status NOT IN ('cancelled','completed'))""",
            (reminder_id, MAX_REMINDER_ATTEMPTS))
        if cur.rowcount != 1:
            return None
        cur = await conn.execute("SELECT attempts FROM reminders WHERE id=?", (reminder_id,))
        row = await cur.fetchone()
        return row["attempts"]


async def _mark_terminal(reminder_id: int, reason: str) -> None:
    await db.execute(
        f"UPDATE reminders SET terminal_reason=?, {db.touch()} "
        "WHERE id=? AND sent=0 AND terminal_reason IS NULL",
        (reason, reminder_id))


async def _retry_or_stop(reminder_id: int, attempt: int) -> int | None:
    if attempt >= MAX_REMINDER_ATTEMPTS:
        await _mark_terminal(reminder_id, "attempt_limit")
        return None
    delay = RETRY_DELAYS_MINUTES[attempt - 1]
    await db.execute(
        f"UPDATE reminders SET send_at=datetime('now', ?), {db.touch()} "
        "WHERE id=? AND sent=0 AND terminal_reason IS NULL",
        (f"+{delay} minutes", reminder_id))
    return delay


async def process_due_reminders(bot: Bot) -> int:
    """Обрабатывает все созревшие напоминания. Возвращает их число (удобно для тестов)."""
    due = await db.fetch_all(
        """SELECT * FROM reminders
           WHERE sent=0 AND terminal_reason IS NULL AND send_at <= datetime('now')
           ORDER BY send_at""")
    for r in due:
        attempt = await _claim_reminder(r["id"])
        if attempt is None:
            current = await db.fetch_one("SELECT attempts FROM reminders WHERE id=?", (r["id"],))
            if current and current["attempts"] >= MAX_REMINDER_ATTEMPTS:
                await _mark_terminal(r["id"], "attempt_limit")
                log.warning("Reminder %s for order %s stopped after %d attempts",
                            r["id"], r["order_id"], current["attempts"])
            else:
                order = await get_order_full(r["order_id"])
                if order is None:
                    await _mark_terminal(r["id"], "order_missing")
                elif order["status"] in ("cancelled", "completed"):
                    await _mark_terminal(r["id"], f"order_{order['status']}")
            continue
        try:
            delivered, terminal_reason = await _send_one(bot, r)
        except TelegramForbiddenError:
            delivered, terminal_reason = False, "forbidden"
        except Exception as exc:
            log.warning("Reminder %s (%s) for order %s raised %s on attempt %d/%d",
                        r["id"], r["kind"], r["order_id"], type(exc).__name__,
                        attempt, MAX_REMINDER_ATTEMPTS)
            delivered, terminal_reason = False, None

        if delivered:
            await db.execute(
                f"UPDATE reminders SET sent=1, {db.touch()} "
                "WHERE id=? AND sent=0 AND terminal_reason IS NULL", (r["id"],))
        elif terminal_reason:
            await _mark_terminal(r["id"], terminal_reason)
            if terminal_reason == "forbidden":
                log.warning("Reminder %s (%s) for order %s not retried: Telegram forbids delivery",
                            r["id"], r["kind"], r["order_id"])
            else:
                log.info("Reminder %s (%s) for order %s stopped: %s",
                         r["id"], r["kind"], r["order_id"], terminal_reason)
        else:
            delay = await _retry_or_stop(r["id"], attempt)
            if delay is None:
                log.warning("Reminder %s (%s) for order %s stopped after %d attempts",
                            r["id"], r["kind"], r["order_id"], attempt)
            else:
                log.warning("Reminder %s (%s) for order %s failed on attempt %d/%d; retry in %d minute(s)",
                            r["id"], r["kind"], r["order_id"], attempt,
                            MAX_REMINDER_ATTEMPTS, delay)
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
