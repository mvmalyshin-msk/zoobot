"""Удаление данных по просьбе клиента («удали меня», /delete_me): личное стирается, заказы остаются для учёта."""
import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
_TMP = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_TMP, "erase.db")

import db  # noqa: E402
import repo  # noqa: E402


class U:  # минимальный «пользователь Telegram»
    def __init__(self, uid, username):
        self.id, self.username = uid, username


async def seed(status="completed"):
    await db.init_db()
    for table in ("reviews", "reminders", "order_statuses_log", "payments", "orders", "pets", "consents", "users"):
        await db.execute(f"DELETE FROM {table}")  # база может быть общей с другими тестами
    u = await repo.get_or_create_user(U(501, "anna_k"))
    await repo.update_user(u["id"], name="Анна", phone="79001234567", address="Ленина 1, кв. 5", district="Химки")
    await repo.save_consent(u, "pdn", True, "v1")
    pet = await repo.add_pet(u["id"], "Рекс", "собака", "лабрадор", "L", "аллергия на курицу")
    svc = await db.fetch_one("SELECT id FROM services LIMIT 1")
    await db.execute(
        "INSERT INTO orders (user_id, pet_id, service_id, status, start_at, address, address_to, price_total) "
        "VALUES (?,?,?,?,?,?,?,?)", (u["id"], pet, svc["id"], status, "2026-11-01T10:00:00", "Ленина 1", "Мира 2", 1500))
    order = await db.fetch_one("SELECT id FROM orders")
    await db.execute("INSERT INTO reviews (order_id, user_id, rating, text) VALUES (?,?,?,?)",
                     (order["id"], u["id"], 5, "Звонила на 79001234567"))
    return u


class Erase(unittest.TestCase):
    def setUp(self):
        for s in ("", "-wal", "-shm"):
            try:
                os.remove(os.environ["DB_PATH"] + s)
            except FileNotFoundError:
                pass

    def test_personal_data_is_wiped_but_orders_stay(self):
        async def go():
            u = await seed()
            self.assertEqual(await repo.active_order_count(u["id"]), 0)
            await repo.erase_user(u["id"])
            row = await db.fetch_one("SELECT * FROM users WHERE id=?", (u["id"],))
            for col in ("name", "phone", "address", "district", "tg_username"):
                self.assertIsNone(row[col], col)
            self.assertEqual((row["pdn_consent"], row["marketing_consent"]), (0, 0))
            pet = await db.fetch_one("SELECT * FROM pets WHERE user_id=?", (u["id"],))
            self.assertIsNone(pet["breed"])
            self.assertIsNone(pet["notes"])
            self.assertEqual(pet["is_active"], 0)
            self.assertNotEqual(pet["name"], "Рекс")
            o = await db.fetch_one("SELECT * FROM orders")
            self.assertIsNone(o["address"])
            self.assertIsNone(o["address_to"])
            self.assertEqual(o["price_total"], 1500)  # учёт сохраняется
            self.assertIsNone((await db.fetch_one("SELECT text FROM reviews"))["text"])
            last = await db.fetch_one("SELECT granted FROM consents ORDER BY id DESC LIMIT 1")
            self.assertEqual(last["granted"], 0)  # отзыв согласия записан в журнал
        asyncio.run(go())

    def test_active_orders_are_counted(self):
        async def go():
            u = await seed(status="assigned")
            self.assertEqual(await repo.active_order_count(u["id"]), 1)
        asyncio.run(go())

    def test_other_users_untouched(self):
        async def go():
            u = await seed()
            other = await repo.get_or_create_user(U(502, "boris"))
            await repo.update_user(other["id"], name="Борис", phone="79007654321")
            await repo.erase_user(u["id"])
            row = await repo.get_user(other["id"])
            self.assertEqual((row["name"], row["phone"], row["tg_username"]), ("Борис", "79007654321", "boris"))
        asyncio.run(go())


class Phrase(unittest.TestCase):
    def test_phrases(self):
        import texts
        for t in ("удали меня", "Удалите меня", "удалить мои данные", "удалите мои персональные данные",
                  "удали мои данные!", "хочу удалить свои данные", "/delete_me"):
            self.assertTrue(texts.is_erase_request(t), t)
        for t in ("удали собаку", "меня зовут Анна", "Ленина 5", "", "удали"):
            self.assertFalse(texts.is_erase_request(t), t)


if __name__ == "__main__":
    unittest.main()
