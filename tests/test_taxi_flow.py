"""Зоотакси (просьба владельца): сначала «Куда подать машину?», затем «Куда поедем?».
Подтверждение адреса с карты называет, какой это адрес — подачи или назначения."""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_taxi_flow_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import orders  # noqa: E402
import texts  # noqa: E402
from config import config  # noqa: E402
from handlers import client as h  # noqa: E402
from handlers.client import Order  # noqa: E402

LOOP = asyncio.new_event_loop()
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=config.tz)


def run(fn):
    async def go():
        await db.init_db()
        return await fn()
    return LOOP.run_until_complete(go())


async def _taxi_state(uid, address):
    await db.execute("DELETE FROM users WHERE tg_id=?", (uid,))
    await db.execute("INSERT INTO users (tg_id, name, phone, address) VALUES (?,?,?,?)",
                     (uid, "Максим", "+79001234567", address))
    svc = await db.fetch_one("SELECT id FROM services WHERE active=1 AND category='taxi' ORDER BY id LIMIT 1")
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    await state.update_data(service_id=svc["id"])
    return state


def _msg(uid):
    m = mock.MagicMock()
    m.answer = mock.AsyncMock()
    m.from_user = mock.MagicMock(id=uid, username=None, first_name="Максим")
    return m


def _said(m):
    return [c.args[0] for c in m.answer.await_args_list]


class PickupFirstTest(unittest.TestCase):
    def test_first_question_is_where_to_send_the_car(self):
        async def go():
            state = await _taxi_state(8801, "Химки, ул. Ленина, 5")
            m = _msg(8801)
            with mock.patch.object(h, "now_utc", return_value=NOW):
                ok = await h._accept_when(m, state, m.from_user, NOW + timedelta(hours=2))
            return ok, _said(m), await state.get_state()
        ok, said, st = run(go)
        self.assertTrue(ok)
        self.assertEqual(st, Order.point_a.state)
        self.assertIn("Куда подать машину?", said[0])
        self.assertEqual(said[1], texts.ASK_POINT_A_OTHER)

    def test_without_a_saved_address_the_bot_asks_once_and_does_not_fail(self):
        async def go():
            state = await _taxi_state(8802, None)
            m = _msg(8802)
            with mock.patch.object(h, "now_utc", return_value=NOW):
                await h._accept_when(m, state, m.from_user, NOW + timedelta(hours=2))
            return _said(m)
        said = run(go)
        self.assertEqual(said, [texts.ASK_POINT_A_NO_SAVED])
        self.assertIn("Куда подать машину?", said[0])

    def test_after_pickup_the_bot_asks_where_we_go(self):
        async def go():
            state = await _taxi_state(8803, "Химки, ул. Ленина, 5")
            await state.set_state(Order.point_a)
            cb = mock.MagicMock()
            cb.answer = mock.AsyncMock()
            cb.from_user = mock.MagicMock(id=8803, username=None)
            cb.message = _msg(8803)
            await h.cb_point_a_profile(cb, state)
            return _said(cb.message), await state.get_state()
        said, st = run(go)
        self.assertEqual(st, Order.point_b.state)
        self.assertEqual(said, [texts.ASK_POINT_B])
        self.assertIn("Куда поедем?", texts.ASK_POINT_B)


class MapPickNamesTheAddressTest(unittest.TestCase):
    def _pick(self, step):
        async def go():
            state = await _taxi_state(8804, "Химки, ул. Ленина, 5")
            await state.set_state(step)
            m = _msg(8804)
            m.web_app_data = mock.MagicMock(data=json.dumps({"lat": 55.87, "lon": 37.66, "label": "Ясный проезд, 14", "flat": ""}))
            with mock.patch.object(geo, "reverse", mock.AsyncMock(return_value=None)):
                await h.addr_webapp(m, state)
            return _said(m)[0]
        return run(go)

    def test_pickup_and_destination_are_named(self):
        self.assertTrue(self._pick(Order.point_a).startswith("Проверьте адрес подачи машины"))
        self.assertTrue(self._pick(Order.point_b).startswith("Проверьте, куда поедем"))


class OrderCardTest(unittest.TestCase):
    def test_taxi_card_says_pickup_and_destination(self):
        o = {"id": 9, "status": "created", "service_name": "Зоотакси", "start_at": "2026-11-09 07:00:00", "end_at": None,
             "unit": "поездка", "address": "Химки, ул. Ленина, 5", "address_to": "Москва, Ясный проезд, 14",
             "outside_mkad": 0, "pet_name": None, "price_total": 1200}
        card = orders.order_card(o)
        self.assertIn("Подача: Химки, ул. Ленина, 5", card)
        self.assertIn("Куда: Москва, Ясный проезд, 14", card)
        self.assertNotIn("Откуда:", card)


def tearDownModule():
    LOOP.close()


if __name__ == "__main__":
    unittest.main()
