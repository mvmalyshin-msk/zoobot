"""Сайт и бот не обещают того, чего сервис не делает (ТЗ владельца, п.10; ответ владельца 15.11.2026:
исполнители формально не проверяются — «замени как ты предлагаешь»).

Нельзя: «проверенные специалисты», «проверка исполнителя входит в цену», «замена, если что-то пошло не так».
Можно: «подбираем специалиста под вашего питомца»."""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_honest_claims_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import texts  # noqa: E402
import webserver  # noqa: E402

FORBIDDEN = re.compile(r"проверенн|проверк[аиу] исполнител|проверяем специалист|находим и проверяем|и замен[ау], если",
                       re.IGNORECASE)


class NoUnbackedPromises(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        app = webserver.make_app()
        app["bot"] = None
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def test_every_page_in_sitemap(self):
        sm = await (await self.client.get("/sitemap.xml")).text()
        paths = [re.sub(r"^https?://[^/]+", "", u) or "/" for u in re.findall(r"<loc>([^<]+)</loc>", sm)]
        self.assertGreater(len(paths), 20)
        for path in paths + ["/request", "/chat/login"]:
            t = await (await self.client.get(path)).text()
            m = FORBIDDEN.search(t)
            self.assertIsNone(m, f"{path}: «{m.group(0) if m else ''}»")

    def test_bot_texts(self):
        for name in dir(texts):
            v = getattr(texts, name)
            if isinstance(v, str):
                self.assertIsNone(FORBIDDEN.search(v), name)

    async def test_honest_phrase_is_used(self):
        t = await (await self.client.get("/")).text()
        self.assertIn("Подбираем специалистов под вашего питомца", t)


if __name__ == "__main__":
    unittest.main()
