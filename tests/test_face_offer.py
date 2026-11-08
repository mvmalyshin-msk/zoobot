"""После входа по коду портал сразу предлагает включить Face ID одной кнопкой (как у «Моего помощника»),
если у администратора ещё нет ни одного ключа."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_face_offer_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import passkeys  # noqa: E402
import portal  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
ADMIN = min(config.admin_ids)


def run(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM passkeys")
        portal._hits.clear()
        portal._fails.clear()
        return await fn()
    return LOOP.run_until_complete(go())


async def login(c):
    code = await portal._issue_code(ADMIN, "tg")
    return await c.post("/portal/verify", data={"code": code}, allow_redirects=False)


class FaceOfferTest(unittest.TestCase):
    def test_first_code_login_leads_to_the_face_id_offer(self):
        async def go():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                r = await login(c)
                page = await c.get("/portal/face")
                return r.status, r.headers.get("Location"), page.status, await page.text()
        status, loc, pstatus, html = run(go)
        self.assertEqual((status, loc), (303, "/portal/face"))
        self.assertEqual(pstatus, 200)
        self.assertIn('id="pkon"', html)                 # одна большая кнопка «Включить Face ID»
        self.assertIn('href="/portal"', html)            # и «Не сейчас»

    def test_admin_with_a_key_goes_straight_to_the_portal(self):
        async def go():
            await passkeys.add(ADMIN, b"cred-1", b"pub-1", "iPhone")
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                r = await login(c)
                return r.status, r.headers.get("Location")
        self.assertEqual(run(go), (303, "/portal"))

    def test_offer_page_needs_a_login(self):
        async def go():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                page = await c.get("/portal/face")
                return await page.text()
        self.assertIn("Войти по Face ID", run(go))       # без входа — обычная страница входа


if __name__ == "__main__":
    unittest.main()
