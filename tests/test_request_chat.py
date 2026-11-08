"""Заявка с сайта без входа: клиент получает свою гостевую сессию (устройство + IP запоминаются), админ пишет ему в чат,
разные клиенты не путаются; на стартовой странице заметная кнопка «Чат»."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_request_chat_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0 Mobile Safari/537.36"
GOOD = {"service_id": "1", "name": "Анна", "phone": "8 900 777-88-99", "address": "Москва, ул. Ленина, 1",
        "when": "завтра в 10:00", "pet": "Дымок", "comment": "", "contact": "telegram", "consent": "on", "site": ""}


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


class RequestChatTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        import webform
        webform.RATE.clear()
        await db.init_db()
        self.bot = FakeBot()
        app = webserver.make_app()
        app["bot"] = self.bot
        self.client = TestClient(TestServer(app))
        await self.client.start_server()
        self.admin = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def send(self, data=None, ua=IPHONE, ip="10.0.0.1", cookie=None):
        h = {"User-Agent": ua, "X-Forwarded-For": ip}
        if cookie:
            h["Cookie"] = f"{webchat.COOKIE}={cookie}"
        self.client.session.cookie_jar.clear()
        r = await self.client.post("/request", data=data or GOOD, headers=h)
        c = r.cookies.get(webchat.COOKIE)
        return r, (c.value if c else None)

    async def row(self, rid=None):
        sql = "SELECT * FROM web_requests ORDER BY id DESC LIMIT 1" if rid is None else f"SELECT * FROM web_requests WHERE id={rid}"
        return await db.fetch_one(sql)

    async def test_request_creates_guest_session_with_device_and_ip(self):
        r, token = await self.send()
        self.assertEqual(r.status, 200)
        self.assertTrue(token, "клиенту выдана cookie-сессия")
        row = await self.row()
        self.assertLess(row["web_uid"], 0)
        self.assertEqual(row["ip"], "10.0.0.1")
        self.assertIn("iPhone", row["device"])
        s = await db.fetch_one("SELECT * FROM web_sessions WHERE token_hash=?", (webchat._hash(token),))
        self.assertEqual((s["uid"], s["ip"]), (row["web_uid"], "10.0.0.1"))
        u = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (row["web_uid"],))
        self.assertEqual((u["name"], u["phone"]), ("Анна", "+79007778899"))

    async def test_admin_gets_chat_link_and_thread_has_request(self):
        await self.send()
        row = await self.row()
        text = next(t for c, t in self.bot.sent if c == 111)
        self.assertIn(f"/portal/chat/{row['web_uid']}", text)
        msgs = await db.fetch_all("SELECT * FROM messages WHERE party_tg_id=?", (row["web_uid"],))
        self.assertTrue(any("Заявка с сайта" in m["text"] for m in msgs))
        self.assertTrue(any(c == row["web_uid"] for c, _ in self.bot.sent), "клиенту в чат ушло подтверждение")

    async def test_done_page_offers_chat(self):
        r, _ = await self.send()
        self.assertIn('href="/chat"', await r.text())

    async def test_same_browser_keeps_one_client_other_browser_is_separate(self):
        _, tok = await self.send()
        first = await self.row()
        _, again = await self.send(cookie=tok)
        self.assertIsNone(again, "тому же браузеру новая сессия не нужна")
        self.assertEqual((await self.row())["web_uid"], first["web_uid"])
        _, tok2 = await self.send(ua=ANDROID, ip="10.0.0.2")          # тот же номер, другое устройство
        other = await self.row()
        self.assertNotEqual(other["web_uid"], first["web_uid"])
        self.assertIn("Android", other["device"])

    async def test_logged_in_telegram_client_is_linked_not_replaced(self):
        await db.execute("INSERT OR IGNORE INTO users (tg_id, name) VALUES (777, 'Борис')")
        tok = await webchat.new_session(777)
        _, new_cookie = await self.send(cookie=tok)
        self.assertEqual((await self.row())["web_uid"], 777)
        self.assertIsNone(new_cookie)

    async def test_admin_request_page_shows_chat_device_ip_and_same_phone(self):
        await self.send()
        await self.send(ua=ANDROID, ip="10.0.0.2")
        row = await self.row()
        t = await (await self.client.get(f"/portal/request/{row['id']}", headers=self.admin)).text()
        self.assertIn(f'href="/portal/chat/{row["web_uid"]}"', t)
        self.assertIn("Android", t)
        self.assertIn("10.0.0.2", t)
        self.assertIn("Другие заявки с этого номера", t)

    async def test_admin_message_reaches_the_right_client_only(self):
        _, tok = await self.send()
        a = (await self.row())["web_uid"]
        await self.send(ua=ANDROID, ip="10.0.0.2")
        b = (await self.row())["web_uid"]
        self.bot.sent.clear()
        r = await self.client.post(f"/portal/api/chat/{a}", json={"text": "Здравствуйте!"}, headers={**self.admin, "X-Zc": "1"})
        self.assertEqual(r.status, 200)
        self.assertEqual([c for c, t in self.bot.sent if "Здравствуйте" in t], [a])
        self.assertNotIn(b, [c for c, _ in self.bot.sent])

    async def test_chat_visit_refreshes_ip_of_session(self):
        _, tok = await self.send()
        await self.client.get("/chat", headers={"Cookie": f"{webchat.COOKIE}={tok}", "X-Forwarded-For": "10.0.0.9", "User-Agent": IPHONE})
        s = await db.fetch_one("SELECT ip FROM web_sessions WHERE token_hash=?", (webchat._hash(tok),))
        self.assertEqual(s["ip"], "10.0.0.9")

    async def test_home_has_prominent_chat_button(self):
        t = await (await self.client.get("/")).text()
        hero = t.split("</header>")[0]
        self.assertRegex(hero, r'<a[^>]*href="/chat"[^>]*>[^<]*Чат')


if __name__ == "__main__":
    unittest.main()
