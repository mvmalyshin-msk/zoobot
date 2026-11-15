"""В диалоге бота мат на шаге сбора данных не доходит до обработчика; без шага и команды проходят."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

from handlers.guard import ProfanityGuard  # noqa: E402


class St:
    def __init__(self, s):
        self.s = s

    async def get_state(self):
        return self.s


class Msg:
    def __init__(self, text):
        self.text, self.said = text, []

    async def answer(self, t, **kw):
        self.said.append(t)


class Guard(unittest.IsolatedAsyncioTestCase):
    async def run_one(self, text, state):
        calls = []

        async def handler(ev, data):
            calls.append(ev.text)
            return "handled"

        m = Msg(text)
        res = await ProfanityGuard()(handler, m, {"state": St(state)})
        return res, calls, m.said

    async def test_profanity_in_step_is_blocked(self):
        res, calls, said = await self.run_one("пиздец какой двор", "Order:comment")
        self.assertIsNone(res)
        self.assertEqual(calls, [])
        self.assertIn("нецензурн", said[0])

    async def test_clean_text_in_step_passes(self):
        res, calls, said = await self.run_one("Химки, улица Победы, 3", "Order:point_a")
        self.assertEqual((res, calls, said), ("handled", ["Химки, улица Победы, 3"], []))

    async def test_message_without_step_passes(self):
        res, calls, _ = await self.run_one("пиздец", None)
        self.assertEqual(res, "handled")

    async def test_commands_pass(self):
        res, calls, _ = await self.run_one("/menu", "Order:comment")
        self.assertEqual(res, "handled")


if __name__ == "__main__":
    unittest.main()
