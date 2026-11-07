"""Личный кабинет на сайте: один клиент — один кабинет (питомцы и история заказов).
Веб-гость, привязанный к Telegram (verified_tg_id), видит тот же кабинет, что и клиент в Telegram."""
import asyncio
import itertools
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_cabinet_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import repo  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
_ids = itertools.count(8000)


async def _client(tg_id: int, name: str, pet: str | None = None, order: bool = False) -> int:
    await db.execute("INSERT INTO users (tg_id, name) VALUES (?, ?)", (tg_id, name))
    user = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (tg_id,))
    if pet:
        pet_id = await repo.add_pet(user["id"], pet, "кошка", None, "S", None)
    if order:
        svc = await db.fetch_one("SELECT id FROM services WHERE active=1 ORDER BY id LIMIT 1")
        oid = await orders.create_order(
            {"user_id": user["id"], "pet_id": pet_id if pet else None, "service_id": svc["id"],
             "start_at": datetime.now(timezone.utc) + timedelta(days=3), "end_at": None, "quantity": 1,
             "address": "Сходня, Ленинградское ш., 1", "is_urgent": False, "outside_mkad": False},
            1000, [["Тестовая услуга", 1000]], "client")
        return oid
    return user["id"]


def run(coro_fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_sessions")
        webchat._linked_cache.clear()
        webchat._tg_cache.clear()
        app = webserver.make_app()
        async with TestClient(TestServer(app)) as c:
            return await coro_fn(c)
    return LOOP.run_until_complete(go())


async def _get(c, token=None):
    headers = {"Cookie": f"{webchat.COOKIE}={token}"} if token else {}
    return await c.get("/cabinet", headers=headers, allow_redirects=False)


class CabinetAccessTest(unittest.TestCase):
    def test_without_session_asks_to_open_from_telegram_or_link(self):
        async def go(c):
            r = await _get(c)
            return r.status, await r.text()
        status, body = run(go)
        self.assertEqual(status, 401)
        self.assertIn("Telegram", body)


class CabinetLinkTest(unittest.TestCase):
    def test_chat_page_links_to_the_cabinet(self):
        async def go(c):
            r = await c.get("/chat", allow_redirects=False)
            return await r.text()
        self.assertIn('href="/cabinet"', run(go))


class CabinetContentTest(unittest.TestCase):
    def test_client_sees_own_pets_and_orders_with_status(self):
        tg = next(_ids)
        async def go(c):
            oid = await _client(tg, "Анна", pet="Барсик", order=True)
            await _client(next(_ids), "Чужой", pet="Чужой кот")
            token = await webchat.new_session(tg)
            r = await _get(c, token)
            return r.status, await r.text(), oid
        status, body, oid = run(go)
        self.assertEqual(status, 200)
        self.assertIn("Барсик", body)
        self.assertIn(f"№{oid}", body)
        self.assertIn("Заявка принята", body)
        self.assertNotIn("Чужой кот", body)

    def test_web_guest_linked_to_telegram_sees_the_same_cabinet(self):
        tg = next(_ids)
        async def go(c):
            await _client(tg, "Ольга", pet="Рыжик")
            guest_token = await webchat.new_session()  # гость: отрицательный uid
            row = await db.fetch_one("SELECT uid FROM web_sessions WHERE token_hash=?",
                                     (webchat._hash(guest_token),))
            guest_uid = row["uid"]
            await db.execute("INSERT INTO users (tg_id, verified_tg_id) VALUES (?, ?)", (guest_uid, tg))
            webchat._tg_cache.clear()
            r = await _get(c, guest_token)
            return r.status, await r.text()
        status, body = run(go)
        self.assertEqual(status, 200)
        self.assertIn("Рыжик", body)

    def test_guest_without_link_sees_empty_cabinet_not_someone_elses(self):
        tg = next(_ids)
        async def go(c):
            await _client(tg, "Кто-то", pet="Тайный пёс")
            token = await webchat.new_session()
            r = await _get(c, token)
            return r.status, await r.text()
        status, body = run(go)
        self.assertEqual(status, 200)
        self.assertNotIn("Тайный пёс", body)
        self.assertIn("Питомцев пока нет", body)


if __name__ == "__main__":
    unittest.main()
