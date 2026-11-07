"""Описание бота на стартовой странице: лимиты Telegram и то, что бот его реально задаёт при запуске."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")

import bot  # noqa: E402
import texts  # noqa: E402


class FakeBot:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    async def set_my_description(self, t):
        if self.fail:
            raise RuntimeError("tg down")
        self.calls.append(("d", t))

    async def set_my_short_description(self, t):
        self.calls.append(("s", t))


class ProfileTexts(unittest.TestCase):
    def test_limits(self):
        self.assertLessEqual(len(texts.BOT_DESCRIPTION), 512)
        self.assertLessEqual(len(texts.BOT_SHORT_DESCRIPTION), 120)

    def test_no_garbage(self):
        self.assertNotIn("TRAS", texts.BOT_DESCRIPTION)

    def test_set_on_start(self):
        b = FakeBot()
        asyncio.run(bot.set_profile_texts(b))
        self.assertEqual(b.calls, [("d", texts.BOT_DESCRIPTION), ("s", texts.BOT_SHORT_DESCRIPTION)])

    def test_failure_does_not_crash(self):
        asyncio.run(bot.set_profile_texts(FakeBot(fail=True)))


if __name__ == "__main__":
    unittest.main()
