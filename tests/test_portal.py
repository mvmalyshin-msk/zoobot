"""Портал администратора /portal: вход (код в Telegram, код на почту), все заявки из всех источников,
карточка заказа с действиями, переписка, защита от чужих."""
import asyncio
import os
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_portal_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import mailer  # noqa: E402
import portal  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

TG = []
MAIL = []
LOOP = asyncio.new_event_loop()


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            TG.append((method.chat_id, method.text))
        return Message(message_id=len(TG), date=datetime.now(), chat=Chat(id=getattr(method, "chat_id", 0) or 0, type="private"))


async def fake_send(to, subject, body):
    MAIL.append((to, subject, body))
    return True

_n = [0]


async def fixtures():
    """Клиент из Telegram, клиент из веб-чата, заявка с формы, исполнитель — каждый тест со своими."""
    _n[0] += 1
    k = _n[0] * 10
    tg, web = 70000 + k, -(70000 + k)
    u1 = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, ?, '+79001112233', 1)",
                          (tg, '<script>alert(1)</script>Ира'))
    u2 = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent) VALUES (?, 'Веб Вера', '+79004445566', 1)", (web,))
    sid = (await db.fetch_one("SELECT id FROM services WHERE category='walk' LIMIT 1"))["id"]
    o1 = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total) VALUES (?,?, '2030-01-01 10:00:00', 900)", (u1, sid))
    await db.execute("INSERT INTO payments (order_id, amount) VALUES (?, 900)", (o1,))
    o2 = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, status) VALUES (?,?, '2030-01-02 10:00:00', 700, 'paid')", (u2, sid))
    await db.execute("INSERT INTO payments (order_id, amount, status) VALUES (?, 700, 'paid')", (o2,))
    wr = await db.execute("INSERT INTO web_requests (service_id, name, phone, address) VALUES (?, 'Форма Фёдор', '+79007778899', 'Мира 1')", (sid,))
    ex = await db.execute("INSERT INTO executors (tg_id, name, legal_status, services) VALUES (?, 'Олег', 'individual', 'walk')", (80000 + k,))
    return {"tg": tg, "web": web, "o1": o1, "o2": o2, "wr": wr, "ex": ex, "ex_tg": 80000 + k}


def run(fn, admin=True):
    async def go():
        await db.init_db()
        TG.clear()
        MAIL.clear()
        portal._hits.clear()
        f = await fixtures()
        bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, None
        async with TestClient(TestServer(app)) as c:
            if admin:
                tok = await webchat.new_session(111)
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: tok})
            return await fn(c, f)
    return LOOP.run_until_complete(go())


H = {"X-Zc": "1"}


class AccessTest(unittest.TestCase):
    def test_anonymous_sees_login_not_data(self):
        async def t(c, f):
            for path in ("/portal", f"/portal/order/{f['o1']}", "/portal/clients", f"/portal/chat/{f['tg']}"):
                r = await c.get(path)
                html = await r.text()
                self.assertIn("Вход в портал", html, path)
                self.assertNotIn("+79001112233", html, path)
            r = await c.post(f"/portal/order/{f['o1']}", data={"action": "confirm"})
            self.assertEqual(r.status, 403)
            self.assertEqual((await db.fetch_one("SELECT status FROM orders WHERE id=?", (f["o1"],)))["status"], "created")
        run(t, admin=False)

    def test_web_guest_is_not_admin(self):
        async def t(c, f):
            await c.get("/chat")  # гостевая сессия
            self.assertIn("Вход в портал", await (await c.get("/portal")).text())
        run(t, admin=False)

    def test_foreign_origin_post_rejected(self):
        async def t(c, f):
            r = await c.post(f"/portal/order/{f['o1']}", data={"action": "confirm"}, headers={"Origin": "https://evil.example"})
            self.assertEqual(r.status, 403)
        run(t)


class LoginTest(unittest.TestCase):
    def test_telegram_code(self):
        async def t(c, f):
            r = await c.post("/portal/code", data={"via": "tg"})
            self.assertEqual(r.status, 200)
            msg = next(t for to, t in TG if to == 111)
            code = re.search(r"\b(\d{6})\b", msg).group(1)
            bad = await c.post("/portal/verify", data={"code": "000000" if code != "000000" else "111111"}, allow_redirects=False)
            self.assertEqual(bad.status, 200)
            self.assertIn("не подошёл", await bad.text())
            ok = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
            self.assertEqual(ok.status, 303)
            self.assertIn("+79001112233", await (await c.get("/portal?f=all")).text())
            again = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
            self.assertEqual(again.status, 200, "код одноразовый")
        run(t, admin=False)

    def test_code_dies_after_five_wrong_tries_and_expires(self):
        async def t(c, f):
            await c.post("/portal/code", data={"via": "tg"})
            code = re.search(r"\b(\d{6})\b", next(t for to, t in TG if to == 111)).group(1)
            wrong = "%06d" % ((int(code) + 1) % 1000000)
            for _ in range(5):
                await c.post("/portal/verify", data={"code": wrong})
            r = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
            self.assertEqual(r.status, 200, "после 5 ошибок код больше не действует")
            TG.clear()
            portal._hits.clear()
            await c.post("/portal/code", data={"via": "tg"})
            code = re.search(r"\b(\d{6})\b", next(t for to, t in TG if to == 111)).group(1)
            await db.execute("UPDATE login_codes SET expires_at=datetime('now','-1 minutes')")
            r = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
            self.assertEqual(r.status, 200, "просроченный код не действует")
        run(t, admin=False)

    def test_email_code_only_to_registered_address(self):
        async def t(c, f):
            orig_send, orig_conf = mailer.send, mailer.configured
            mailer.send, mailer.configured = fake_send, lambda: True
            try:
                await db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES ('admin_email:111', 'boss@example.com')")
                r = await c.post("/portal/code", data={"via": "email", "email": "stranger@example.com"})
                self.assertEqual(MAIL, [])
                self.assertIn("если адрес", (await r.text()).lower())
                await c.post("/portal/code", data={"via": "email", "email": " Boss@Example.com "})
                self.assertEqual(MAIL[0][0], "boss@example.com")
                code = re.search(r"\b(\d{6})\b", MAIL[0][2]).group(1)
                ok = await c.post("/portal/verify", data={"code": code}, allow_redirects=False)
                self.assertEqual(ok.status, 303)
            finally:
                mailer.send, mailer.configured = orig_send, orig_conf
        run(t, admin=False)

    def test_email_not_configured_says_so(self):
        async def t(c, f):
            orig = mailer.configured
            mailer.configured = lambda: False
            try:
                html = await (await c.post("/portal/code", data={"via": "email", "email": "a@b.c"})).text()
                self.assertIn("Почта для входа не настроена", html)
            finally:
                mailer.configured = orig
        run(t, admin=False)

    def test_code_requests_are_rate_limited(self):
        async def t(c, f):
            for _ in range(portal.CODE_LIMIT + 2):
                await c.post("/portal/code", data={"via": "tg"})
            self.assertLessEqual(sum(1 for to, _ in TG if to == 111), portal.CODE_LIMIT)
        run(t, admin=False)


class ListTest(unittest.TestCase):
    def test_all_sources_in_one_list_and_escaped(self):
        async def t(c, f):
            html = await (await c.get("/portal?f=all")).text()
            for marker in (f"№{f['o1']}", f"№{f['o2']}", "Форма Фёдор", "Telegram", "Чат на сайте", "Форма на сайте"):
                self.assertIn(marker, html)
            self.assertNotIn("<script>alert(1)", html)
            self.assertIn("&lt;script&gt;", html)
        run(t)

    def test_new_filter(self):
        async def t(c, f):
            html = await (await c.get("/portal?f=new")).text()
            self.assertIn(f"/portal/order/{f['o1']}", html)
            self.assertNotIn(f"/portal/order/{f['o2']}\"", html, "оплаченный — не «новый»")
        run(t)

    def test_summary(self):
        async def t(c, f):
            j = await (await c.get("/portal/api/summary")).json()
            self.assertGreaterEqual(j["new_orders"], 1)
            self.assertGreaterEqual(j["new_requests"], 1)
            self.assertIn("last_order", j)
        run(t)


class OrderTest(unittest.TestCase):
    def test_confirm_notifies_client(self):
        async def t(c, f):
            card = await (await c.get(f"/portal/order/{f['o1']}")).text()
            self.assertIn("Подтвердить", card)
            r = await c.post(f"/portal/order/{f['o1']}", data={"action": "confirm"}, allow_redirects=False)
            self.assertEqual(r.status, 303)
            self.assertEqual((await db.fetch_one("SELECT status FROM orders WHERE id=?", (f["o1"],)))["status"], "confirmed")
            self.assertTrue(any(to == f["tg"] for to, _ in TG), "клиент узнал о подтверждении")
            log = await db.fetch_one("SELECT actor FROM order_statuses_log WHERE order_id=? ORDER BY id DESC", (f["o1"],))
            self.assertEqual(log["actor"], "portal:111")
            self.assertFalse(any(to == 111 and "ℹ️" in t for to, t in TG), "админу не пишем о его же действии в портале")
        run(t)

    def test_cancel_with_reason_and_bad_action(self):
        async def t(c, f):
            await c.post(f"/portal/order/{f['o1']}", data={"action": "cancel", "reason": "нет исполнителей"})
            o = await db.fetch_one("SELECT status, cancel_reason FROM orders WHERE id=?", (f["o1"],))
            self.assertEqual(o["status"], "cancelled")
            r = await c.post(f"/portal/order/{f['o1']}", data={"action": "rm -rf"}, allow_redirects=False)
            self.assertEqual(r.status, 303)
        run(t)

    def test_assign_and_room(self):
        async def t(c, f):
            card = await (await c.get(f"/portal/order/{f['o2']}")).text()
            self.assertIn("Олег", card, "в карточке оплаченного заказа — выбор исполнителя")
            await c.post(f"/portal/order/{f['o2']}", data={"action": "assign", "executor_id": str(f["ex"])})
            self.assertEqual((await db.fetch_one("SELECT status FROM orders WHERE id=?", (f["o2"],)))["status"], "assigned")
            TG.clear()
            await c.post(f"/portal/order/{f['o2']}", data={"action": "room_open"})
            room = await db.fetch_one("SELECT open FROM rooms WHERE order_id=?", (f["o2"],))
            self.assertEqual(room["open"], 1)
            self.assertTrue(any(to == f["ex_tg"] for to, _ in TG))
            card = await (await c.get(f"/portal/order/{f['o2']}")).text()
            self.assertIn("Закрыть чат", card)
        run(t)

    def test_payment_link(self):
        async def t(c, f):
            await c.post(f"/portal/order/{f['o1']}", data={"action": "confirm"})
            TG.clear()
            await c.post(f"/portal/order/{f['o1']}", data={"action": "link", "link": "https://pay.example/abc"})
            self.assertEqual((await db.fetch_one("SELECT payment_link FROM payments WHERE order_id=?", (f["o1"],)))["payment_link"],
                             "https://pay.example/abc")
            self.assertTrue(any("Ссылка для оплаты" in t for to, t in TG if to == f["tg"]), "клиент получил ссылку")
            r = await c.post(f"/portal/order/{f['o1']}", data={"action": "link", "link": "javascript:alert(1)"}, allow_redirects=False)
            self.assertEqual(r.status, 303)
            self.assertNotIn("javascript", (await db.fetch_one("SELECT payment_link FROM payments WHERE order_id=?", (f["o1"],)))["payment_link"])
        run(t)

    def test_missing_order(self):
        async def t(c, f):
            self.assertEqual((await c.get("/portal/order/999999")).status, 404)
        run(t)


class RequestTest(unittest.TestCase):
    def test_web_request_status_and_note(self):
        async def t(c, f):
            page = await (await c.get(f"/portal/request/{f['wr']}")).text()
            self.assertIn("tel:+79007778899", page)
            await c.post(f"/portal/request/{f['wr']}", data={"status": "work", "note": "перезвонить в 18:00"})
            row = await db.fetch_one("SELECT status, admin_note FROM web_requests WHERE id=?", (f["wr"],))
            self.assertEqual((row["status"], row["admin_note"]), ("work", "перезвонить в 18:00"))
            await c.post(f"/portal/request/{f['wr']}", data={"status": "hacked"})
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (f["wr"],)))["status"], "work")
        run(t)

    def test_web_request_can_be_cancelled_with_one_button(self):
        async def t(c, f):
            url = f"/portal/request/{f['wr']}"
            page = await (await c.get(url)).text()
            self.assertIn("Отменить заявку", page)
            self.assertIn('name="cancel"', page)
            r = await c.post(url, data={"cancel": "1", "note": "клиент передумал"}, allow_redirects=False)
            self.assertEqual(r.status, 303)
            row = await db.fetch_one("SELECT status, admin_note FROM web_requests WHERE id=?", (f["wr"],))
            self.assertEqual((row["status"], row["admin_note"]), ("rejected", "клиент передумал"))
            page = await (await c.get(url)).text()
            self.assertIn("Заявка отменена", page)
            self.assertNotIn("Отменить заявку", page)
            self.assertIn("Вернуть в работу", page)
            await c.post(url, data={"restore": "1"}, allow_redirects=False)
            self.assertEqual((await db.fetch_one("SELECT status FROM web_requests WHERE id=?", (f["wr"],)))["status"], "new")
        run(t)

    def test_cancel_unknown_request_is_404(self):
        async def t(c, f):
            r = await c.post("/portal/request/999999", data={"cancel": "1"}, allow_redirects=False)
            self.assertIn(r.status, (303, 404))
            self.assertIsNone(await db.fetch_one("SELECT 1 FROM web_requests WHERE id=999999"))
        run(t)


class ChatTest(unittest.TestCase):
    def test_write_to_client_and_see_reply(self):
        async def t(c, f):
            import talk
            r = await c.post(f"/portal/api/chat/{f['tg']}", json={"text": "Здравствуйте!"}, headers=H)
            self.assertEqual(r.status, 200)
            self.assertTrue(any(to == f["tg"] and "Здравствуйте!" in t for to, t in TG))
            await talk.incoming(c.server.app["bot"], f["tg"], "Добрый день")
            j = await (await c.get(f"/portal/api/chat/{f['tg']}?after=0")).json()
            self.assertEqual([m["text"] for m in j["messages"]], ["Здравствуйте!", "Добрый день"])
            self.assertEqual((await c.post(f"/portal/api/chat/{f['tg']}", json={"text": "x"})).status, 403, "без заголовка X-Zc нельзя")
        run(t)

    def test_clients_and_executors_pages(self):
        async def t(c, f):
            html = await (await c.get("/portal/clients?q=Вера")).text()
            self.assertIn("Веб Вера", html)
            self.assertNotIn("Ира", html)
            self.assertIn("Олег", await (await c.get("/portal/executors")).text())
        run(t)


if __name__ == "__main__":
    unittest.main()
