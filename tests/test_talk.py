"""Переписка через бота: клиент ↔ админ, исполнитель ↔ админ, общий чат клиента и исполнителя по заказу."""
import asyncio
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_talk_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402

import db  # noqa: E402
import talk  # noqa: E402

SENT = []
LOOP = asyncio.new_event_loop()


class Fake(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            SENT.append((method.chat_id, method.text, method.reply_markup))
        return Message(message_id=len(SENT), date=datetime.now(), chat=Chat(id=getattr(method, "chat_id", 0), type="private"))


BOT = Bot("1:x", session=Fake())


def run(coro):
    return LOOP.run_until_complete(coro)


_n = [0]


async def setup_order():
    """Свои клиент, исполнитель и заказ на каждый тест: другие тесты в общем прогоне чистят таблицы."""
    await db.init_db()
    _n[0] += 1
    c, e = 50000 + _n[0], 60000 + _n[0]
    uid = await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (?, 'Анна', '+79000000500')", (c,))
    ex = await db.execute("INSERT INTO executors (tg_id, name, legal_status, services) VALUES (?, 'Олег', 'individual', 'walk')", (e,))
    sid = (await db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
    oid = await db.execute("INSERT INTO orders (user_id, service_id, start_at, executor_id, status) VALUES (?,?,?,?, 'assigned')",
                           (uid, sid, "2030-01-01 10:00:00", ex))
    return oid, c, e


class TalkTest(unittest.TestCase):
    def setUp(self):
        global OID, C, E
        OID, C, E = run(setup_order())
        run(db.execute("DELETE FROM messages"))
        run(db.execute("DELETE FROM rooms"))
        SENT.clear()

    def test_client_message_goes_to_admin_and_is_logged(self):
        run(talk.incoming(BOT, C, "Когда приедете?"))
        to_admin = [t for c, t, _ in SENT if c == 111]
        self.assertTrue(any("Когда приедете?" in t and "Анна" in t for t in to_admin), SENT)
        row = run(db.fetch_one("SELECT * FROM messages"))
        self.assertEqual((row["party_tg_id"], row["party_kind"], row["direction"]), (C, "client", "in"))
        kb = [b for c, t, m in SENT if c == 111 for row_ in m.inline_keyboard for b in row_]
        self.assertTrue(any(b.callback_data == f"wc:{C}" for b in kb), "под сообщением кнопка «Ответить»")

    def test_executor_is_recognised(self):
        run(talk.incoming(BOT, E, "Не могу найти подъезд"))
        row = run(db.fetch_one("SELECT * FROM messages"))
        self.assertEqual(row["party_kind"], "executor")
        self.assertTrue(any("Исполнитель" in t and "Олег" in t for c, t, _ in SENT if c == 111))

    def test_admin_reply_reaches_party_and_is_logged(self):
        ok = run(talk.admin_say(BOT, C, "Будем в 10:00", author="admin:111"))
        self.assertTrue(ok)
        self.assertTrue(any(c == C and "Будем в 10:00" in t and "Администратор" in t for c, t, _ in SENT))
        row = run(db.fetch_one("SELECT * FROM messages"))
        self.assertEqual(row["direction"], "out")

    def test_room_relays_both_ways_and_admin_sees_it(self):
        ok, _ = run(talk.open_room(BOT, OID))
        self.assertTrue(ok)
        self.assertTrue(any(c == C and "исполнител" in t.lower() for c, t, _ in SENT), "клиента предупредили")
        self.assertTrue(any(c == E and "клиент" in t.lower() for c, t, _ in SENT), "исполнителя предупредили")
        SENT.clear()
        run(talk.incoming(BOT, C, "Код домофона 12"))
        self.assertTrue(any(c == E and "Код домофона 12" in t for c, t, _ in SENT), "исполнитель получил сообщение клиента")
        self.assertFalse(any(c == 111 for c, t, _ in SENT), "админа не дёргаем каждым сообщением, он видит всё в портале")
        run(talk.incoming(BOT, E, "Понял, буду"))
        self.assertTrue(any(c == C and "Понял, буду" in t for c, t, _ in SENT), "клиент получил ответ исполнителя")
        rows = run(talk.room_log(OID))
        self.assertEqual([r["text"] for r in rows], ["Код домофона 12", "Понял, буду"])
        run(talk.close_room(BOT, OID))
        SENT.clear()
        run(talk.incoming(BOT, C, "ещё вопрос"))
        self.assertFalse(any(c == E for c, t, _ in SENT), "после закрытия чат не пересылается исполнителю")
        self.assertTrue(any(c == 111 for c, t, _ in SENT), "а уходит админу")

    def test_room_stops_when_order_is_done(self):
        run(talk.open_room(BOT, OID))
        run(db.execute("UPDATE orders SET status='completed' WHERE id=?", (OID,)))
        SENT.clear()
        self.assertEqual(run(talk.incoming(BOT, C, "спасибо!")), "admin", "после выполнения заказа чат не пересылается")
        self.assertFalse(any(c == E for c, t, _ in SENT))

    def test_room_needs_executor_with_telegram(self):
        oid2 = run(db.execute("INSERT INTO orders (user_id, service_id, start_at, status) "
                              "SELECT user_id, service_id, start_at, 'paid' FROM orders WHERE id=?", (OID,)))
        ok, why = run(talk.open_room(BOT, oid2))
        self.assertFalse(ok)
        self.assertIn("исполнител", why.lower())

    def test_long_text_is_cut(self):
        run(talk.incoming(BOT, C, "я" * 5000))
        self.assertLessEqual(len(run(db.fetch_one("SELECT text FROM messages"))["text"]), 2000)


if __name__ == "__main__":
    unittest.main()
