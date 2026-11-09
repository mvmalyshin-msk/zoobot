"""Копия каждого уведомления админу уходит ещё и в бот «Мой помощник» (workbot, POST /api/zoo-notify)."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ["ADMIN_IDS"] = "111"
os.environ["DB_PATH"] = "/tmp/zoobot_helper_copy_test.db"

import orders  # noqa: E402
import webpush  # noqa: E402

KEY = "s" * 32
URL = "https://helper.example/api/zoo-notify"


class FakeBot:
    async def send_message(self, uid, text, reply_markup=None):
        pass


class T(unittest.TestCase):
    def setUp(self):
        self.old = {k: os.environ.get(k) for k in ("HELPER_NOTIFY_URL", "SITE_LEADS_KEY")}
        os.environ["HELPER_NOTIFY_URL"], os.environ["SITE_LEADS_KEY"] = URL, KEY
        self.calls = []
        self.saved = (orders._helper_post, webpush.notify)

        async def fake_push(uid, text, title=None):
            return {}
        webpush.notify = fake_push

    def tearDown(self):
        orders._helper_post, webpush.notify = self.saved
        for k, v in self.old.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v

    def run_case(self, code=200, copy=True):
        async def fake(url, text, key):
            self.calls.append((url, text, key))
            if code == "boom":
                raise ConnectionError("нет связи")
            return code
        orders._helper_post = fake

        async def go():
            kw = {} if copy else {"copy_helper": False}
            await orders.notify_admins(FakeBot(), "🆕 Заявка №7\nУслуга: выгул", **kw)
            await asyncio.sleep(0.05)
        asyncio.run(go())

    def test_copy_is_sent(self):
        self.run_case()
        self.assertEqual(len(self.calls), 1)
        url, text, key = self.calls[0]
        self.assertEqual((url, key), (URL, KEY))
        self.assertIn("Заявка №7", text)

    def test_off_without_url(self):
        os.environ.pop("HELPER_NOTIFY_URL")
        self.run_case()
        self.assertEqual(self.calls, [])

    def test_off_without_key(self):
        os.environ["SITE_LEADS_KEY"] = "short"
        self.run_case()
        self.assertEqual(self.calls, [])

    def test_http_url_refused(self):
        os.environ["HELPER_NOTIFY_URL"] = "http://helper.example/api/zoo-notify"
        self.run_case()
        self.assertEqual(self.calls, [])

    def test_copy_can_be_skipped(self):
        self.run_case(copy=False)
        self.assertEqual(self.calls, [])

    def test_failure_does_not_break_notification(self):
        self.run_case(code="boom")
        self.run_case(code=502)
        self.assertEqual(len(self.calls), 2)

    def test_site_lead_skips_copy(self):
        import inspect
        import siteleads
        self.assertIn("copy_helper=False", inspect.getsource(siteleads.post_lead))


if __name__ == "__main__":
    unittest.main()
