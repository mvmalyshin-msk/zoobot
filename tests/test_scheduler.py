import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import db  # noqa: E402
import orders  # noqa: E402
import scheduler  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from config import config  # noqa: E402


class ReminderDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_db_path = config.db_path
        object.__setattr__(config, "db_path", os.path.join(self.temp_dir.name, "test.db"))
        await db.init_db()
        self.service = await db.fetch_one("SELECT id FROM services WHERE code='walk30'")

    async def asyncTearDown(self):
        object.__setattr__(config, "db_path", self.previous_db_path)
        self.temp_dir.cleanup()

    async def add_reminder(self, status="paid", kind="client_2h"):
        user_id = await db.execute("INSERT INTO users (tg_id, name) VALUES (?, ?)",
                                   (10000 + (await db.fetch_one("SELECT COUNT(*) n FROM users"))["n"], "Тест"))
        order_id = await db.execute(
            """INSERT INTO orders (user_id, service_id, status, start_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, self.service["id"], status,
             datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
        reminder_id = await db.execute(
            "INSERT INTO reminders (order_id, kind, send_at) VALUES (?, ?, datetime('now', '-1 minute'))",
            (order_id, kind))
        return reminder_id, order_id

    async def reminder(self, reminder_id):
        return await db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))

    async def test_marks_sent_only_after_notify_confirms_success(self):
        reminder_id, _ = await self.add_reminder()
        with mock.patch.object(scheduler, "notify", mock.AsyncMock(return_value=True)) as notify:
            await scheduler.process_due_reminders(None)
        row = await self.reminder(reminder_id)
        self.assertEqual(row["sent"], 1)
        self.assertEqual(row["attempts"], 1)
        notify.assert_awaited_once()

    async def test_retryable_failure_is_deferred_and_retried(self):
        reminder_id, _ = await self.add_reminder()
        with mock.patch.object(scheduler, "notify", mock.AsyncMock(return_value=False)) as notify:
            await scheduler.process_due_reminders(None)
            failed = await self.reminder(reminder_id)
            self.assertEqual(failed["sent"], 0)
            self.assertEqual(failed["attempts"], 1)
            self.assertIsNone(failed["terminal_reason"])
            self.assertGreater(failed["send_at"], datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "))

            await scheduler.process_due_reminders(None)
            self.assertEqual(notify.await_count, 1)
            await db.execute("UPDATE reminders SET send_at=datetime('now', '-1 minute') WHERE id=?",
                             (reminder_id,))
            notify.return_value = True
            await scheduler.process_due_reminders(None)

        row = await self.reminder(reminder_id)
        self.assertEqual(row["sent"], 1)
        self.assertEqual(row["attempts"], 2)

    async def test_forbidden_delivery_is_terminal_without_retry(self):
        reminder_id, _ = await self.add_reminder()
        forbidden = TelegramForbiddenError(
            method=SendMessage(chat_id=10000, text="reminder"), message="Forbidden")
        with mock.patch.object(scheduler, "notify", mock.AsyncMock(side_effect=forbidden)) as notify:
            await scheduler.process_due_reminders(None)
            row = await self.reminder(reminder_id)
            self.assertEqual(row["sent"], 0)
            self.assertEqual(row["terminal_reason"], "forbidden")
            await scheduler.process_due_reminders(None)
        self.assertEqual(notify.await_count, 1)

    async def test_attempt_cap_stops_retries(self):
        reminder_id, _ = await self.add_reminder()
        with mock.patch.object(scheduler, "notify", mock.AsyncMock(return_value=False)) as notify:
            for _ in range(scheduler.MAX_REMINDER_ATTEMPTS):
                await scheduler.process_due_reminders(None)
                await db.execute("UPDATE reminders SET send_at=datetime('now', '-1 minute') WHERE id=?",
                                 (reminder_id,))
            await scheduler.process_due_reminders(None)
        row = await self.reminder(reminder_id)
        self.assertEqual(row["sent"], 0)
        self.assertEqual(row["attempts"], scheduler.MAX_REMINDER_ATTEMPTS)
        self.assertEqual(row["terminal_reason"], "attempt_limit")
        self.assertEqual(notify.await_count, scheduler.MAX_REMINDER_ATTEMPTS)

    async def test_cancelled_and_completed_orders_are_not_notified(self):
        cancelled_id, _ = await self.add_reminder(status="cancelled")
        completed_id, _ = await self.add_reminder(status="completed")
        with mock.patch.object(scheduler, "notify", mock.AsyncMock()) as notify:
            await scheduler.process_due_reminders(None)
        cancelled = await self.reminder(cancelled_id)
        completed = await self.reminder(completed_id)
        self.assertEqual(cancelled["sent"], 0)
        self.assertEqual(cancelled["terminal_reason"], "order_cancelled")
        self.assertEqual(completed["sent"], 0)
        self.assertEqual(completed["terminal_reason"], "order_completed")
        self.assertEqual(cancelled["attempts"], 0)
        self.assertEqual(completed["attempts"], 0)
        notify.assert_not_awaited()

    async def test_terminal_status_is_rechecked_before_send(self):
        reminder_id, _ = await self.add_reminder()
        active = {
            "id": 1, "status": "paid", "client_tg_id": 10000, "start_at": "2026-11-14 12:00:00",
            "service_name": "Выгул", "executor_tg_id": None, "address": None,
        }
        completed = {**active, "status": "completed"}
        with mock.patch.object(scheduler, "get_order_full", mock.AsyncMock(side_effect=[active, completed])), \
                mock.patch.object(scheduler, "notify", mock.AsyncMock()) as notify:
            await scheduler.process_due_reminders(None)
        row = await self.reminder(reminder_id)
        self.assertEqual(row["sent"], 0)
        self.assertEqual(row["terminal_reason"], "order_completed")
        notify.assert_not_awaited()

    async def test_terminal_order_transition_stops_pending_reminders_without_marking_sent(self):
        reminder_ids = []
        for status in ("cancelled", "completed"):
            reminder_id, order_id = await self.add_reminder(status="paid")
            reminder_ids.append((reminder_id, order_id, status))
        for _, order_id, status in reminder_ids:
            async with db.transaction() as conn:
                await orders._transition(conn, order_id, "paid", status, "system")
        for reminder_id, _, status in reminder_ids:
            row = await self.reminder(reminder_id)
            self.assertEqual(row["sent"], 0)
            self.assertEqual(row["terminal_reason"], f"order_{status}")

    async def test_payment_stops_only_payment_reminder(self):
        pay_id, order_id = await self.add_reminder(status="confirmed", kind="pay_3h")
        client_id = await db.execute(
            "INSERT INTO reminders (order_id, kind, send_at) VALUES (?, 'client_24h', datetime('now'))",
            (order_id,))
        async with db.transaction() as conn:
            await orders._transition(conn, order_id, "confirmed", "paid", "system")
        pay = await self.reminder(pay_id)
        client = await self.reminder(client_id)
        self.assertEqual(pay["sent"], 0)
        self.assertEqual(pay["terminal_reason"], "payment_received")
        self.assertEqual(client["sent"], 0)
        self.assertIsNone(client["terminal_reason"])

    async def test_rescheduling_resets_retry_state(self):
        reminder_id, order_id = await self.add_reminder()
        await db.execute(
            "UPDATE reminders SET attempts=?, terminal_reason='forbidden' WHERE id=?",
            (scheduler.MAX_REMINDER_ATTEMPTS, reminder_id))
        await orders.schedule_reminder(order_id, "client_2h", datetime.now(timezone.utc))
        row = await self.reminder(reminder_id)
        self.assertEqual(row["sent"], 0)
        self.assertEqual(row["attempts"], 0)
        self.assertIsNone(row["terminal_reason"])


if __name__ == "__main__":
    unittest.main()
