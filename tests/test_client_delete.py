"""Удаление клиента из админки: каскад без хвостов, защита админа/исполнителя/активных заказов, окно подтверждения."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_client_delete_test.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import repo  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
N = [7000]


def run(coro):
    return LOOP.run_until_complete(coro)


async def count(sql, params=()):
    return (await db.fetch_one(sql, params))["n"]


async def make_client(status=None, paid=False, tg=None, phone="+79990001122"):
    """Клиент с питомцем, согласием, перепиской, сессией и (если нужно) заказом в статусе status."""
    N[0] += 1
    tg = tg or N[0]
    uid = await db.execute("INSERT INTO users (tg_id, name, phone, address, pdn_consent) VALUES (?,?,?,?,1)", (tg, "Тест Тестов", phone, "Мира 1"))
    await db.execute("INSERT INTO pets (user_id, name, species) VALUES (?,?,?)", (uid, "Рекс", "собака"))
    await db.execute("INSERT INTO consents (user_id, tg_id, type, granted, text_version) VALUES (?,?,?,?,?)", (uid, tg, "pdn", 1, "1"))
    await db.execute("INSERT INTO messages (party_tg_id, party_kind, direction, text) VALUES (?,?,?,?)", (tg, "client", "in", "привет"))
    await db.execute("INSERT INTO web_sessions (token_hash, uid) VALUES (?,?)", (f"h{tg}", tg))
    await db.execute("INSERT INTO push_subs (uid, endpoint, p256dh, auth) VALUES (?,?,?,?)", (tg, f"e{tg}", "p", "a"))
    await db.execute("INSERT INTO web_requests (name, phone, address, when_text, web_uid) VALUES (?,?,?,?,?)", ("Тест", phone, "Мира 1", "сегодня", tg))
    oid = None
    if status:
        sid = (await db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
        oid = await db.execute("INSERT INTO orders (user_id, service_id, status, start_at, quantity, address, price_total) VALUES (?,?,?,?,?,?,?)",
                               (uid, sid, status, "2026-12-01 10:00:00", 1, "Мира 1", 900))
        await db.execute("INSERT INTO order_statuses_log (order_id, to_status, actor) VALUES (?,?,?)", (oid, status, "t"))
        await db.execute("INSERT INTO payments (order_id, amount, status) VALUES (?,?,?)", (oid, 900, "paid" if paid else "pending"))
    return uid, tg, oid


class Cascade(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(os.environ["DB_PATH"]):
            os.remove(os.environ["DB_PATH"])
        run(db.init_db())

    def test_finished_client_is_removed_with_everything(self):
        async def go():
            uid, tg, oid = await make_client("cancelled")
            other, otg, _ = await make_client()
            code, _msg = await repo.delete_client(uid)
            left = {
                "users": await count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,)),
                "pets": await count("SELECT COUNT(*) AS n FROM pets WHERE user_id=?", (uid,)),
                "consents": await count("SELECT COUNT(*) AS n FROM consents WHERE user_id=?", (uid,)),
                "orders": await count("SELECT COUNT(*) AS n FROM orders WHERE user_id=?", (uid,)),
                "log": await count("SELECT COUNT(*) AS n FROM order_statuses_log WHERE order_id=?", (oid,)),
                "pay": await count("SELECT COUNT(*) AS n FROM payments WHERE order_id=?", (oid,)),
                "msg": await count("SELECT COUNT(*) AS n FROM messages WHERE party_tg_id=?", (tg,)),
                "sess": await count("SELECT COUNT(*) AS n FROM web_sessions WHERE uid=?", (tg,)),
                "push": await count("SELECT COUNT(*) AS n FROM push_subs WHERE uid=?", (tg,)),
                "req": await count("SELECT COUNT(*) AS n FROM web_requests WHERE web_uid=?", (tg,)),
            }
            mine = await count("SELECT COUNT(*) AS n FROM pets WHERE user_id=?", (other,))
            return code, left, mine
        code, left, mine = run(go())
        self.assertEqual(code, "deleted")
        self.assertEqual({k: v for k, v in left.items() if v}, {})
        self.assertEqual(mine, 1)  # чужой клиент не тронут

    def test_admin_is_protected(self):
        uid, tg, _ = run(make_client(tg=111))
        code, msg = run(repo.delete_client(uid))
        self.assertEqual(code, "blocked")
        self.assertIn("администратор", msg.lower())
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,))), 1)

    def test_executor_is_protected(self):
        uid, tg, _ = run(make_client())
        run(db.execute("INSERT INTO executors (tg_id, name, legal_status) VALUES (?,?,?)", (tg, "Исп", "self_employed")))
        code, msg = run(repo.delete_client(uid))
        self.assertEqual(code, "blocked")
        self.assertIn("исполнител", msg.lower())
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,))), 1)

    def test_active_order_blocks_and_names_it(self):
        uid, tg, oid = run(make_client("assigned"))
        code, msg = run(repo.delete_client(uid))
        self.assertEqual(code, "blocked")
        self.assertIn(f"№{oid}", msg)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM orders WHERE id=?", (oid,))), 1)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM pets WHERE user_id=?", (uid,))), 1)

    def test_paid_history_is_kept_but_personal_data_is_erased(self):
        uid, tg, oid = run(make_client("completed", paid=True))
        code, msg = run(repo.delete_client(uid))
        self.assertEqual(code, "erased")
        u = run(db.fetch_one("SELECT * FROM users WHERE id=?", (uid,)))
        self.assertIsNone(u["name"])
        self.assertIsNone(u["phone"])
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM orders WHERE id=?", (oid,))), 1)  # учёт сохранён
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM payments WHERE order_id=?", (oid,))), 1)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM messages WHERE party_tg_id=?", (tg,))), 0)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM web_sessions WHERE uid=?", (tg,))), 0)

    def test_unknown_client(self):
        code, _ = run(repo.delete_client(987654321))
        self.assertEqual(code, "missing")

    def test_deletion_is_logged(self):
        uid, tg, _ = run(make_client("cancelled"))
        run(repo.delete_client(uid, admin_tg_id=111))
        row = run(db.fetch_one("SELECT * FROM admin_log WHERE command='client_delete' ORDER BY id DESC LIMIT 1"))
        self.assertIsNotNone(row)
        self.assertEqual(row["admin_tg_id"], 111)
        self.assertNotIn("Тест Тестов", row["args"] or "")  # в журнале нет личных данных


class Portal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run(db.init_db())

    def with_admin(self, fn):
        async def go():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
                return await fn(c)
        return run(go())

    def test_list_has_trash_button_and_confirm_modal(self):
        uid, tg, _ = run(make_client("cancelled"))

        async def fn(c):
            return await (await c.get("/portal/clients")).text()
        html = self.with_admin(fn)
        self.assertIn("Вы уверены, что хотите удалить клиента?", html)
        self.assertIn(f'data-del="{uid}"', html)
        self.assertIn('action="/portal/clients/delete"', html)
        self.assertIn("безвозвратно", html)

    def test_post_deletes_and_flashes(self):
        uid, tg, _ = run(make_client("cancelled"))

        async def fn(c):
            r = await c.post("/portal/clients/delete", data={"id": str(uid)}, allow_redirects=False)
            return r.status, r.headers["Location"]
        status, loc = self.with_admin(fn)
        self.assertEqual(status, 303)
        self.assertTrue(loc.startswith("/portal/clients"))
        self.assertIn("m=", loc)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,))), 0)

    def test_post_needs_admin(self):
        uid, tg, _ = run(make_client("cancelled"))

        async def go():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                return (await c.post("/portal/clients/delete", data={"id": str(uid)}, allow_redirects=False)).status
        self.assertEqual(run(go()), 403)
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,))), 1)

    def test_post_rejects_foreign_origin_and_junk_id(self):
        uid, tg, _ = run(make_client("cancelled"))

        async def fn(c):
            a = await c.post("/portal/clients/delete", data={"id": str(uid)}, headers={"Origin": "https://evil.example"}, allow_redirects=False)
            b = await c.post("/portal/clients/delete", data={"id": "abc"}, allow_redirects=False)
            return a.status, b.status
        a, b = self.with_admin(fn)
        self.assertEqual(a, 403)
        self.assertEqual(b, 303)  # мусорный id — сообщение «не найден», ничего не падает
        self.assertEqual(run(count("SELECT COUNT(*) AS n FROM users WHERE id=?", (uid,))), 1)


if __name__ == "__main__":
    unittest.main()
