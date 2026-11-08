"""Связь с Telegram через прокси: нужна, когда бот стоит на российском сервере, откуда Telegram напрямую не открывается."""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import tgnet  # noqa: E402


class ProxyTest(unittest.TestCase):
    def test_no_proxy_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TELEGRAM_PROXY", None)
            self.assertIsNone(tgnet.proxy_url())
            self.assertIsNone(tgnet.make_session()._proxy)

    def test_proxy_from_variable(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_PROXY": " socks5://user:pw@1.2.3.4:1080 "}):
            self.assertEqual(tgnet.proxy_url(), "socks5://user:pw@1.2.3.4:1080")
            self.assertEqual(tgnet.make_session()._proxy, "socks5://user:pw@1.2.3.4:1080")

    def test_blank_variable_means_no_proxy(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_PROXY": "   "}):
            self.assertIsNone(tgnet.proxy_url())

    def test_log_line_never_contains_password(self):
        with mock.patch.dict(os.environ, {"TELEGRAM_PROXY": "socks5://user:secretpw@1.2.3.4:1080"}):
            line = tgnet.describe()
        self.assertIn("1.2.3.4:1080", line)
        self.assertNotIn("secretpw", line)
        self.assertNotIn("user", line)


if __name__ == "__main__":
    unittest.main()
