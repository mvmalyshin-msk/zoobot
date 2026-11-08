"""Телефон сообщает серверу, почему Face ID не сработал: в журнале видно имя ошибки, лишнего не пишется, спамить нельзя."""
import logging
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_pkerr_test.db"

import db  # noqa: E402
import portal  # noqa: E402
import webserver  # noqa: E402


class PasskeyErrTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        portal._pk_hits.clear()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()

    async def send(self, body, headers=None):
        return await self.client.post("/portal/passkey/err", json=body, headers={"X-Zc": "1"} if headers is None else headers)

    async def test_logs_error_name_only(self):
        with self.assertLogs("portal", level=logging.INFO) as cap:
            r = await self.send({"where": "register", "name": "NotAllowedError<script>", "extra": "секрет"})
        self.assertEqual(r.status, 200)
        line = "\n".join(cap.output)
        self.assertIn("NotAllowedErrorscript", line)
        self.assertIn("register", line)
        self.assertNotIn("секрет", line)
        self.assertNotIn("<", line)

    async def test_needs_header_and_is_limited(self):
        self.assertEqual((await self.send({"name": "X"}, headers={})).status, 429)
        for _ in range(30):
            await self.send({"name": "X"})
        self.assertEqual((await self.send({"name": "X"})).status, 429)

    async def test_foreign_origin_refused(self):
        r = await self.send({"name": "X"}, headers={"X-Zc": "1", "Origin": "https://evil.example"})
        self.assertEqual(r.status, 429)


if __name__ == "__main__":
    unittest.main()
