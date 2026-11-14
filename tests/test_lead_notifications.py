import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import db  # noqa: E402
import orders  # noqa: E402
import scheduler  # noqa: E402


class LeadNotificationTest(unittest.TestCase):
    def run_case(self, coro):
        async def go():
            original_config = db.config
            with tempfile.TemporaryDirectory() as directory:
                db.config = replace(original_config, db_path=str(Path(directory) / "leads.db"))
                try:
                    await db.init_db()
                    await coro()
                finally:
                    db.config = original_config
        asyncio.run(go())

    async def create_notification(self):
        request_id = await db.execute(
            """INSERT INTO web_requests (service_id, name, phone, address)
               VALUES (1, 'Тестовый клиент', '+79001234567', 'Москва, Тестовая, 1')""")
        notification_id = await db.execute(
            "INSERT INTO web_request_notifications (web_request_id) VALUES (?)", (request_id,))
        return request_id, notification_id

    def test_admin_notification_reports_telegram_result(self):
        async def case():
            send = mock.AsyncMock(return_value=True)
            with mock.patch.object(orders, "config", mock.Mock(admin_ids={111})), \
                    mock.patch.object(orders, "notify", send):
                self.assertTrue(await orders.notify_admins(object(), "lead", telegram_only=True))
                send.return_value = False
                self.assertFalse(await orders.notify_admins(object(), "lead", telegram_only=True))
            self.assertEqual(send.await_count, 2)
        self.run_case(case)

    def test_success_marks_notification_sent(self):
        async def case():
            _, notification_id = await self.create_notification()
            send = mock.AsyncMock(return_value=True)
            with mock.patch.object(orders, "notify_admins", send):
                result = await scheduler.process_web_request_notification(object(), notification_id)

            row = await db.fetch_one(
                "SELECT status, attempts, last_error, sent_at FROM web_request_notifications WHERE id=?",
                (notification_id,))
            self.assertTrue(result)
            self.assertEqual(row["status"], "sent")
            self.assertEqual(row["attempts"], 1)
            self.assertIsNone(row["last_error"])
            self.assertIsNotNone(row["sent_at"])
            text = send.await_args.args[1]
            self.assertIn("+79001234567", text)
            self.assertIn("Заявка с сайта", text)
        self.run_case(case)

    def test_failures_back_off_and_end_in_visible_terminal_state(self):
        async def case():
            _, notification_id = await self.create_notification()
            send = mock.AsyncMock(return_value=False)
            with mock.patch.object(orders, "notify_admins", send), \
                    self.assertLogs("scheduler", level="ERROR") as captured:
                for attempt in range(1, scheduler.WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS + 1):
                    if attempt > 1:
                        await db.execute(
                            """UPDATE web_request_notifications
                               SET next_attempt_at=datetime('now', '-1 minute') WHERE id=?""",
                            (notification_id,))
                    self.assertEqual(await scheduler.process_due_web_request_notifications(object()), 1)
                    row = await db.fetch_one(
                        """SELECT status, attempts, next_attempt_at, updated_at, last_error
                           FROM web_request_notifications WHERE id=?""",
                        (notification_id,))
                    self.assertEqual(row["attempts"], attempt)
                    if attempt < scheduler.WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS:
                        self.assertEqual(row["status"], "pending")
                        self.assertIsNotNone(row["last_error"])
                        delay = await db.fetch_one(
                            """SELECT (julianday(next_attempt_at)-julianday(updated_at))*1440 AS minutes
                               FROM web_request_notifications WHERE id=?""", (notification_id,))
                        self.assertAlmostEqual(
                            delay["minutes"], scheduler.WEB_REQUEST_NOTIFICATION_RETRY_MINUTES[attempt - 1],
                            places=5)
                    else:
                        self.assertEqual(row["status"], "failed")
                        self.assertIn("Telegram delivery was not confirmed", row["last_error"])

            self.assertEqual(send.await_count, scheduler.WEB_REQUEST_NOTIFICATION_MAX_ATTEMPTS)
            self.assertFalse(send.await_args_list[0].kwargs["telegram_only"])
            self.assertTrue(all(call.kwargs["telegram_only"] for call in send.await_args_list[1:]))
            self.assertTrue(any("permanently failed" in message for message in captured.output))
        self.run_case(case)


if __name__ == "__main__":
    unittest.main()
