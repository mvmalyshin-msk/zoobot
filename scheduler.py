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
import orders
from config import config
from orders import get_order_full, notify
from utils import fmt_local, rub

log = logging.getLogger(__name__)

WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS = 5
WEB_REQUEST_NOTIFICATION_RETRY_MINUTES = (1, 5, 15, 60)
WEB_REQUEST_NOTIFICATION_LEASE_MINUTES = 5
WEB_REQUEST_NOTIFICATION_BATCH_SIZE = 50


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


def web_request_notification_text(request: dict) -> str:
    return "\n".join(filter(None, [
        f"🌐 Заявка с сайта №{request['id']}",
        f"Услуга: {request['service']} (от {rub(request['base_price'])})",
        f"Имя: {request['name']}", f"Телефон: {request['phone']}",
        f"Зарегистрирована: {fmt_local(request['created_at'])}",
        f"Когда: {request['when_text']}" if request["when_text"] else "",
        f"Адрес: {request['address']}",
        f"Питомец: {request['pet']}" if request["pet"] else "",
        f"Комментарий: {request['comment']}" if request["comment"] else "",
        "Свяжитесь с клиентом по телефону или напишите ему в чат (он увидит ответ на сайте):",
        f"{config.webapp_url}/portal/chat/{request['web_uid']}"]))


async def _claim_web_request_notification(notification_id: int | None = None) -> dict | None:
    async with db.transaction(immediate=True) as conn:
        expired = await conn.execute(
            """UPDATE web_request_notifications
               SET status='failed', lease_until=NULL,
                   last_error='Delivery lease expired after maximum attempts',
                   updated_at=datetime('now')
               WHERE status='sending' AND lease_until <= datetime('now') AND attempts >= ?""",
            (WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS,))
        if expired.rowcount:
            log.error("%s website lead notification(s) failed after an expired final attempt",
                      expired.rowcount)

        where_id = "AND n.id=?" if notification_id is not None else ""
        params = [WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS]
        if notification_id is not None:
            params.append(notification_id)
        cur = await conn.execute(
            f"""SELECT n.* FROM web_request_notifications n
                WHERE n.attempts < ?
                  AND ((n.status='pending' AND n.next_attempt_at <= datetime('now'))
                       OR (n.status='sending' AND n.lease_until <= datetime('now')))
                  {where_id}
                ORDER BY n.next_attempt_at, n.id LIMIT 1""",
            tuple(params))
        row = await cur.fetchone()
        if not row:
            return None
        notification = dict(row)
        cur = await conn.execute(
            f"""UPDATE web_request_notifications
                SET status='sending', attempts=attempts+1,
                    lease_until=datetime('now', '+{WEB_REQUEST_NOTIFICATION_LEASE_MINUTES} minutes'),
                    last_error=NULL, updated_at=datetime('now')
                WHERE id=? AND attempts < ?
                  AND ((status='pending' AND next_attempt_at <= datetime('now'))
                       OR (status='sending' AND lease_until <= datetime('now')))""",
            (notification["id"], WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS))
        if cur.rowcount != 1:
            return None
        notification["attempts"] += 1
        return notification


async def _process_web_request_notification(bot: Bot, notification: dict) -> bool:
    notification_id = notification["id"]
    try:
        request = await db.fetch_one(
            """SELECT r.*, s.name AS service, s.base_price
               FROM web_requests r
               JOIN services s ON s.id=r.service_id
               WHERE r.id=?""",
            (notification["web_request_id"],))
        if not request:
            raise LookupError("Lead or its service is missing")
        delivered = await orders.notify_admins(
            bot, web_request_notification_text(request), telegram_only=notification["attempts"] > 1)
        if not delivered:
            raise RuntimeError("Telegram delivery was not confirmed")
    except Exception as exc:
        error = type(exc).__name__[:80] or "UnknownError"
        if notification["attempts"] >= WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS:
            await _finish_web_request_notification(notification_id, "failed", error)
            log.error("Website lead notification %s permanently failed after %s attempts: %s",
                      notification_id, notification["attempts"], error)
        else:
            delay = WEB_REQUEST_NOTIFICATION_RETRY_MINUTES[notification["attempts"] - 1]
            await _retry_web_request_notification(notification_id, delay, error)
            log.warning("Website lead notification %s failed on attempt %s; retry in %s minute(s): %s",
                        notification_id, notification["attempts"], delay, error)
        return False

    await _finish_web_request_notification(notification_id, "sent")
    return True


async def _finish_web_request_notification(notification_id: int, status: str, error: str | None = None) -> None:
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"""UPDATE web_request_notifications
                SET status=?, last_error=?, lease_until=NULL,
                    sent_at=CASE WHEN ?='sent' THEN datetime('now') ELSE NULL END,
                    {db.touch()}
                WHERE id=? AND status='sending'""",
            (status, error, status, notification_id))
        if cur.rowcount != 1:
            raise RuntimeError(f"Could not finalize website lead notification {notification_id}")


async def _retry_web_request_notification(notification_id: int, delay_minutes: int, error: str) -> None:
    async with db.transaction() as conn:
        cur = await conn.execute(
            f"""UPDATE web_request_notifications
                SET status='pending', last_error=?, lease_until=NULL,
                    next_attempt_at=datetime('now', '+{delay_minutes} minutes'),
                    {db.touch()}
                WHERE id=? AND status='sending'""",
            (error, notification_id))
        if cur.rowcount != 1:
            raise RuntimeError(f"Could not schedule retry for website lead notification {notification_id}")


async def process_web_request_notification(bot: Bot, notification_id: int) -> bool:
    """Immediately attempts one newly accepted lead; failures remain queued for the scheduler."""
    notification = await _claim_web_request_notification(notification_id)
    if not notification:
        return False
    return await _process_web_request_notification(bot, notification)


async def process_due_web_request_notifications(bot: Bot) -> int:
    """Attempt a bounded batch of due website lead notifications; retry state is stored in SQLite."""
    attempted = 0
    while attempted < WEB_REQUEST_NOTIFICATION_BATCH_SIZE:
        notification = await _claim_web_request_notification()
        if not notification:
            break
        attempted += 1
        await _process_web_request_notification(bot, notification)
    return attempted


def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(process_due_reminders, "interval", seconds=60, args=[bot],
                      id="reminders", max_instances=1, coalesce=True)
    scheduler.add_job(process_due_web_request_notifications, "interval", seconds=60, args=[bot],
                      id="web_request_notifications", max_instances=1, coalesce=True)
    # отчёт в Excel в конце дня по Москве (пустой день пропускается)
    scheduler.add_job(report.send_daily_report, "cron", hour=23, minute=55, timezone=config.tz, args=[bot],
                      id="daily_report", max_instances=1, coalesce=True, misfire_grace_time=600)
    scheduler.add_job(analytics.cleanup, "cron", hour=3, minute=20, timezone=config.tz,
                      id="analytics_cleanup", max_instances=1, coalesce=True)  # журнал аналитики старше 2 лет
    return scheduler
