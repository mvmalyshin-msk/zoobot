"""Startup must not trigger the one-off data purge."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")

import bot  # noqa: E402
import purge  # noqa: E402
from config import config  # noqa: E402


class StartupSafety(unittest.TestCase):
    def test_main_skips_purge_and_starts_web_and_polling(self):
        async def run():
            with tempfile.TemporaryDirectory() as temp_dir:
                original_db_path = config.db_path
                object.__setattr__(config, "db_path", str(Path(temp_dir) / "startup.db"))
                try:
                    bot_instance = SimpleNamespace(session=SimpleNamespace(close=AsyncMock()))
                    dispatcher = SimpleNamespace(start_polling=AsyncMock())
                    scheduler = Mock()
                    web_runner = SimpleNamespace(cleanup=AsyncMock())

                    with (
                        patch.dict(os.environ, {"PORT": "8000"}),
                        patch.object(bot.db, "init_db", new=AsyncMock()) as init_db,
                        patch.object(bot, "Bot", return_value=bot_instance),
                        patch.object(bot, "AiohttpSession", return_value=object()),
                        patch.object(bot.webchat, "WebRouterSession", return_value=object()),
                        patch.object(bot, "build_dispatcher", return_value=dispatcher),
                        patch.object(bot.webserver, "start", new=AsyncMock(return_value=web_runner)) as start_web,
                        patch.object(bot, "setup_scheduler", return_value=scheduler),
                        patch.object(bot, "set_commands", new=AsyncMock()),
                        patch.object(bot, "set_profile_texts", new=AsyncMock()),
                        patch.object(bot.seo, "indexnow_ping", new=AsyncMock()),
                        patch.object(bot.releases, "announce", new=AsyncMock()),
                        patch.object(purge, "run_once", new=AsyncMock()) as run_purge,
                    ):
                        await bot.main()

                    init_db.assert_awaited_once()
                    start_web.assert_awaited_once_with(8000, bot_instance, dispatcher)
                    scheduler.start.assert_called_once()
                    dispatcher.start_polling.assert_awaited_once_with(bot_instance)
                    run_purge.assert_not_awaited()
                    scheduler.shutdown.assert_called_once_with(wait=False)
                    web_runner.cleanup.assert_awaited_once()
                    bot_instance.session.close.assert_awaited_once()
                finally:
                    object.__setattr__(config, "db_path", original_db_path)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
