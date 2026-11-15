"""Примеры в полях заявки — настоящие образцы (с городом и номером дома), а не описания и не «ветклиника на Ленина, 5»."""
import asyncio
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_form_examples_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import webserver  # noqa: E402


class FormExamples(unittest.TestCase):
    def test_address_examples_are_real_addresses(self):
        async def go():
            await db.init_db()
            app = webserver.make_app()
            app["bot"] = object()
            async with TestClient(TestServer(app)) as c:
                for path in ("/request", "/request?service=taxi"):
                    html = await (await c.get(path)).text()
                    self.assertNotIn("ветклиника на Ленина", html, path)
                    for fid in ("address", "dest"):
                        m = re.search(rf'id="{fid}"[^>]*placeholder="([^"]*)"', html)
                        if m:
                            self.assertTrue(m.group(1).startswith("например, "), (path, fid, m.group(1)))
                            self.assertRegex(m.group(1), r"\d", (path, fid))
                    m = re.search(r'id="name"[^>]*placeholder="([^"]*)"', html)
                    self.assertIsNotNone(m, path)
            await db.close_shared()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
