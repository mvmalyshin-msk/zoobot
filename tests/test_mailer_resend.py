"""Почта через HTTPS-сервис Resend: Railway не пропускает SMTP, а HTTPS проходит."""
import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import mailer  # noqa: E402

SMTP = {"SMTP_HOST": "smtp.example.test", "SMTP_USER": "u@example.test", "SMTP_PASSWORD": "x"}


class ResendTest(unittest.TestCase):
    def test_configured_with_only_resend_key(self):
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}, clear=True):
            self.assertTrue(mailer.configured())
            self.assertEqual(mailer.missing(), [])

    def test_not_configured_without_anything(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(mailer.configured())

    def test_resend_is_preferred_over_smtp(self):
        calls = []

        async def fake(to, subject, body):
            calls.append((to, subject, body))
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_test", **SMTP}, clear=True), \
                mock.patch.object(mailer, "_resend", fake), \
                mock.patch.object(mailer, "_send_sync", side_effect=AssertionError("SMTP не нужен")):
            ok, why = asyncio.run(mailer.send_checked("a@example.test", "Тема", "Текст"))
        self.assertTrue(ok, why)
        self.assertEqual(calls, [("a@example.test", "Тема", "Текст")])

    def test_payload_has_sender_recipient_and_text(self):
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_test"}, clear=True):
            p = mailer.resend_payload("a@example.test", "Тема", "Текст")
        self.assertEqual(p["to"], ["a@example.test"])
        self.assertEqual(p["subject"], "Тема")
        self.assertEqual(p["text"], "Текст")
        self.assertIn("@", p["from"])

    def test_resend_error_is_explained_without_the_key(self):
        async def boom(to, subject, body):
            raise mailer.ResendError(403)
        with mock.patch.dict(os.environ, {"RESEND_API_KEY": "re_secretkey"}, clear=True), \
                mock.patch.object(mailer, "_resend", boom):
            ok, why = asyncio.run(mailer.send_checked("a@example.test", "T", "B"))
        self.assertFalse(ok)
        self.assertNotIn("re_secretkey", why)
        self.assertIn("Resend", why)


if __name__ == "__main__":
    unittest.main()
