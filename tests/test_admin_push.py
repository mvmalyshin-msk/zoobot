"""Каждое уведомление админу (новая заявка, сообщение клиента) дублируется пушем на телефон, а не только в Telegram."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ["ADMIN_IDS"] = "111"
os.environ["DB_PATH"] = "/tmp/zoobot_admin_push_test.db"

import orders  # noqa: E402
import webpush  # noqa: E402


class FakeBot:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send_message(self, uid, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append(uid)


class AdminPushTest(unittest.TestCase):
    def run_case(self, bot, push):
        pushed = []
        orig = webpush.notify

        async def fake(uid, text, title=None):
            pushed.append((uid, text, title))
            if push == "boom":
                raise RuntimeError("push down")
            return {}
        webpush.notify = fake
        try:
            asyncio.run(orders.notify_admins(bot, "🆕 Заявка №5 с сайта"))
        finally:
            webpush.notify = orig
        return pushed

    def test_push_goes_to_admin(self):
        pushed = self.run_case(FakeBot(), "ok")
        self.assertEqual(sorted(p[0] for p in pushed), [111])
        self.assertIn("Заявка №5", pushed[0][1])

    def test_push_even_if_telegram_fails(self):
        bot = FakeBot(fail=True)
        pushed = self.run_case(bot, "ok")
        self.assertEqual(len(pushed), 1)

    def test_push_error_does_not_break_telegram(self):
        bot = FakeBot()
        self.run_case(bot, "boom")
        self.assertEqual(sorted(bot.sent), [111])


class AdminMailTest(unittest.TestCase):
    def run_case(self, address="admin@example.test", configured=True, boom=False):
        import mailer
        import portal
        sent = []
        saved = (mailer.configured, mailer.send_checked, portal.admin_email, webpush.notify)

        async def fake_send(to, subject, body):
            if boom:
                raise RuntimeError("smtp down")
            sent.append((to, subject, body))
            return True, ""

        async def fake_addr(uid):
            return address

        async def fake_push(uid, text, title=None):
            return {}
        mailer.configured, mailer.send_checked = (lambda: configured), fake_send
        portal.admin_email, webpush.notify = fake_addr, fake_push
        bot = FakeBot()

        async def go():
            await orders.notify_admins(bot, "🆕 Заявка №5 с сайта\nУслуга: выгул")
            await asyncio.sleep(0.05)
        try:
            asyncio.run(go())
        finally:
            mailer.configured, mailer.send_checked, portal.admin_email, webpush.notify = saved
        return sent, bot

    def test_request_is_duplicated_by_email(self):
        sent, _ = self.run_case()
        self.assertEqual(len(sent), 1)
        to, subject, body = sent[0]
        self.assertEqual(to, "admin@example.test")
        self.assertIn("Заявка №5", subject)
        self.assertIn("Услуга: выгул", body)

    def test_no_mail_without_address_or_smtp(self):
        self.assertEqual(self.run_case(address=None)[0], [])
        self.assertEqual(self.run_case(configured=False)[0], [])

    def test_mail_error_does_not_break_telegram(self):
        _, bot = self.run_case(boom=True)
        self.assertEqual(bot.sent, [111])


if __name__ == "__main__":
    unittest.main()
