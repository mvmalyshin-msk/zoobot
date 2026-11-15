"""Какая нижняя панель чата висит сейчас: считается по ленте событий, а не по последнему сообщению с панелью."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import webchat  # noqa: E402

P = [[{"t": "📍 Отправить геопозицию", "loc": True}]]


def ev(op, **kw):
    return {"op": op, "mid": 1, **kw}


class Panel(unittest.TestCase):
    def test_set_and_keep(self):
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=True)]), (P, True))
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=True), ev("send", who="bot", text="ещё")]), (P, True))

    def test_user_reply_clears_once_panel(self):
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=True), ev("send", who="me", text="Привет")]), (None, False))

    def test_user_reply_keeps_permanent_panel(self):
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=False), ev("send", who="me", text="Привет")]), (P, False))

    def test_button_press_clears(self):
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=True), ev("reply")]), (None, False))

    def test_remove_command_clears(self):
        self.assertEqual(webchat._panel([ev("send", who="bot", reply=P, once=True), ev("send", who="bot", text="ок", reply=None)]), (None, False))

    def test_new_panel_after_clear(self):
        evs = [ev("send", who="bot", reply=P, once=True), ev("reply"), ev("send", who="bot", reply=P, once=True)]
        self.assertEqual(webchat._panel(evs), (P, True))


if __name__ == "__main__":
    unittest.main()
