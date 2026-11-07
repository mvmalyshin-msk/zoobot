"""Заявки клиента по заказу: «сообщить о проблеме» и «попросить перенос», отмена с сайта,
история статусов. Всё пишется в журнал заказа и уходит администратору; статус при этом не меняется."""
import asyncio
import itertools
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_requests_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import keyboards  # noqa: E402
import orders  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
_ids = itertools.count(9100)


async def _order(tg_id: int, status: str = "created") -> int:
    await db.execute("INSERT OR IGNORE INTO users (tg_id, name) VALUES (?, ?)", (tg_id, "Клиент"))
    user = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (tg_id,))
    svc = await db.fetch_one("SELECT id FROM services WHERE active=1 ORDER BY id LIMIT 1")
    oid = await orders.create_order(
        {"user_id": user["id"], "pet_id": None, "service_id": svc["id"],
         "start_at": datetime.now(timezone.utc) + timedelta(days=3), "end_at": None, "quantity": 1,
         "address": "Химки, ул. Ленина, 1", "is_urgent": False, "outside_mkad": False},
        1000, [["Тестовая услуга", 1000]], "client")
    if status != "created":
        await db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
    return oid


def run(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_sessions")
        webchat._linked_cache.clear()
        webchat._tg_cache.clear()
        return await fn()
    return LOOP.run_until_complete(go())


class ClientRequestTest(unittest.TestCase):
    def test_problem_is_written_to_history_and_sent_to_admin_without_status_change(self):
        tg = next(_ids)
        async def go():
            oid = await _order(tg, "confirmed")
            with mock.patch.object(orders, "notify_admins", mock.AsyncMock()) as notify:
                ok, msg = await orders.client_request(None, oid, "problem", "Собака не пришла на выгул", f"client:{tg}")
            hist = await orders.order_history(oid)
            o = await orders.get_order_full(oid)
            return ok, msg, notify.await_args, hist, o["status"]
        ok, msg, notify, hist, status = run(go)
        self.assertTrue(ok)
        self.assertIn("администратор", msg)
        self.assertIn("Собака не пришла на выгул", notify.args[1])
        self.assertTrue(any("Собака не пришла на выгул" in (h["note"] or "") for h in hist))
        self.assertEqual(status, "confirmed")

    def test_move_request_is_recorded_as_a_request(self):
        tg = next(_ids)
        async def go():
            oid = await _order(tg, "paid")
            with mock.patch.object(orders, "notify_admins", mock.AsyncMock()):
                ok, _ = await orders.client_request(None, oid, "move", "Перенесите на завтра в 10:00", f"client:{tg}")
            return ok, await orders.order_history(oid)
        ok, hist = run(go)
        self.assertTrue(ok)
        self.assertTrue(any("просит перенести" in (h["note"] or "") and "завтра в 10:00" in h["note"] for h in hist))

    def test_empty_or_finished_order_is_refused(self):
        tg = next(_ids)
        async def go():
            oid = await _order(tg, "created")
            done = await _order(tg, "completed")
            with mock.patch.object(orders, "notify_admins", mock.AsyncMock()) as notify:
                empty = await orders.client_request(None, oid, "problem", "   ", "c")
                finished = await orders.client_request(None, done, "problem", "Вопрос", "c")
            return empty, finished, notify.await_count
        empty, finished, sent = run(go)
        self.assertFalse(empty[0])
        self.assertFalse(finished[0])
        self.assertIn("завершён", finished[1])
        self.assertEqual(sent, 0)


class ClientCancelTest(unittest.TestCase):
    def test_client_cancels_own_unpaid_order_and_not_someone_elses(self):
        tg, other = next(_ids), next(_ids)
        async def go():
            oid = await _order(tg, "created")
            await _order(other, "created")
            mine = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (tg,))
            theirs = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (other,))
            foreign_oid = (await db.fetch_one("SELECT id FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1",
                                              (theirs["id"],)))["id"]
            with mock.patch.object(orders, "notify_admins", mock.AsyncMock()), \
                    mock.patch.object(orders, "_after_transition", mock.AsyncMock(return_value=True)):
                ok, _ = await orders.client_cancel(None, oid, mine["id"], f"client:{tg}")
                bad, _ = await orders.client_cancel(None, foreign_oid, mine["id"], f"client:{tg}")
            o = await orders.get_order_full(oid)
            return ok, bad, o["status"]
        ok, bad, status = run(go)
        self.assertTrue(ok)
        self.assertFalse(bad)
        self.assertEqual(status, "cancelled")


class KeyboardTest(unittest.TestCase):
    def test_active_orders_get_problem_and_move_buttons(self):
        kb = keyboards.my_orders([], active_ids=[7])
        data = [b.callback_data for row in kb.inline_keyboard for b in row]
        self.assertIn("req:problem:7", data)
        self.assertIn("req:move:7", data)


class BotRequestFlowTest(unittest.TestCase):
    def test_button_asks_for_text_then_records_it(self):
        from aiogram.fsm.storage.memory import MemoryStorage
        from aiogram.fsm.context import FSMContext
        from aiogram.fsm.storage.base import StorageKey
        from handlers import client as h
        tg = next(_ids)
        async def go():
            oid = await _order(tg, "paid")
            storage = MemoryStorage()
            key = StorageKey(bot_id=1, chat_id=tg, user_id=tg)
            state = FSMContext(storage=storage, key=key)
            cb = mock.MagicMock()
            cb.data = f"req:problem:{oid}"
            cb.from_user = mock.MagicMock(id=tg, username=None, first_name="К")
            cb.answer = mock.AsyncMock()
            cb.message.answer = mock.AsyncMock()
            cb.bot = None
            await h.cb_request_start(cb, state)
            asked = cb.message.answer.await_args.args[0]
            st = await state.get_state()
            msg = mock.MagicMock()
            msg.text = "Замок на калитке заклинило"
            msg.from_user = cb.from_user
            msg.answer = mock.AsyncMock()
            msg.bot = None
            with mock.patch.object(orders, "notify_admins", mock.AsyncMock()):
                await h.msg_request_text(msg, state)
            hist = await orders.order_history(oid)
            return asked, st, await state.get_state(), hist
        asked, st_before, st_after, hist = run(go)
        self.assertIn("Опишите", asked)
        self.assertEqual(st_before, h.RequestOrder.wait.state)
        self.assertIsNone(st_after)
        self.assertTrue(any("заклинило" in (x["note"] or "") for x in hist))


class CabinetActionsTest(unittest.TestCase):
    def _post(self, c, token, oid, data, origin=None):
        headers = {"Cookie": f"{webchat.COOKIE}={token}", "X-Zc": "1"}
        if origin:
            headers["Origin"] = origin
        return c.post(f"/cabinet/order/{oid}", data=data, headers=headers, allow_redirects=False)

    def test_site_cancel_problem_and_history(self):
        tg = next(_ids)
        async def go(c):
            oid = await _order(tg, "created")
            token = await webchat.new_session(tg)
            r1 = await self._post(c, token, oid, {"action": "problem", "text": "Не могу дозвониться"})
            page1 = await (await c.get("/cabinet", headers={"Cookie": f"{webchat.COOKIE}={token}"})).text()
            r2 = await self._post(c, token, oid, {"action": "cancel"})
            o = await orders.get_order_full(oid)
            return r1.status, page1, r2.status, o["status"], oid
        with mock.patch.object(orders, "notify_admins", mock.AsyncMock()), \
                mock.patch.object(orders, "_after_transition", mock.AsyncMock(return_value=True)):
            s1, page1, s2, status, oid = run_client(go)
        self.assertEqual(s1, 302)
        self.assertIn("Не могу дозвониться", page1)
        self.assertEqual(s2, 302)
        self.assertEqual(status, "cancelled")

    def test_foreign_origin_is_refused(self):
        tg = next(_ids)
        async def go(c):
            oid = await _order(tg, "created")
            token = await webchat.new_session(tg)
            r = await self._post(c, token, oid, {"action": "cancel"}, origin="https://evil.example")
            o = await orders.get_order_full(oid)
            return r.status, o["status"]
        status_code, st = run_client(go)
        self.assertEqual(status_code, 403)
        self.assertEqual(st, "created")


def run_client(fn):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_sessions")
        webchat._linked_cache.clear()
        webchat._tg_cache.clear()
        app = webserver.make_app()
        async with TestClient(TestServer(app)) as c:
            return await fn(c)
    return LOOP.run_until_complete(go())


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
