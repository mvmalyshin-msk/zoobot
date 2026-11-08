"""Один администратор (первый в ADMIN_IDS) и режим «войти как клиент» для проверки."""
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ["ADMIN_IDS"] = "111"
os.environ["DB_PATH"] = "/tmp/zoobot_single_admin.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402


class SingleAdminTest(unittest.TestCase):
    def test_only_first_id_is_admin(self):
        code = "import config;print(sorted(config.config.admin_ids))"
        env = dict(os.environ, ADMIN_IDS="777, 111,222")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "[777]")


class ClientModeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.admin = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def test_menu_has_client_mode(self):
        t = await (await self.client.get("/portal", headers=self.admin)).text()
        self.assertRegex(t, r'<form method="post" action="/portal/as-client"')

    async def test_as_client_gives_client_session_and_keeps_admin(self):
        origin = f"http://{self.client.host}:{self.client.port}"
        r = await self.client.post("/portal/as-client", headers=dict(self.admin, Origin=origin), allow_redirects=False)
        self.assertEqual(r.status, 302)
        self.assertEqual(r.headers["Location"], "/")
        self.assertIn(webchat.COOKIE, r.cookies)
        tok = r.cookies[webchat.COOKIE].value
        row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?", (webchat._hash(tok),))
        self.assertLess(row["uid"], 0)          # обычный гость, не админ

    async def test_anonymous_cannot(self):
        r = await self.client.post("/portal/as-client", allow_redirects=False)
        self.assertEqual(r.status, 403)


if __name__ == "__main__":
    unittest.main()
