"""Ежедневный отчёт для админа в Excel: новые клиенты и заказы за день (без сети и Telegram)."""
import asyncio
import io
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "report.db"))

import openpyxl  # noqa: E402

import db  # noqa: E402
import report  # noqa: E402

DAY = date(2026, 11, 7)  # по Москве: 07.11 00:00 = 06.11 21:00 UTC


async def reset():
    await db.init_db()
    for t in ("reviews", "reminders", "order_statuses_log", "payments", "orders", "pets", "consents", "users"):
        await db.execute(f"DELETE FROM {t}")


async def add_user(tg, created, name="Анна", phone="79001112233", username="anna"):
    await db.execute("INSERT INTO users (tg_id, tg_username, name, phone, address, district, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (tg, username, name, phone, "Ленина 1", "Химки", created))
    return (await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (tg,)))["id"]


def rows(data: bytes, sheet: str):
    wb = openpyxl.load_workbook(io.BytesIO(data))
    return [[c.value for c in r] for r in wb[sheet].iter_rows()]


class Daily(unittest.TestCase):
    def test_only_clients_of_that_local_day(self):
        async def go():
            await reset()
            await add_user(1, "2026-11-06 20:59:59", name="Вчера")      # 23:59:59 по Москве 06.11
            await add_user(2, "2026-11-06 21:00:00", name="Начало")     # 00:00 07.11
            await add_user(3, "2026-11-07 20:59:59", name="Конец")      # 23:59:59 07.11
            await add_user(4, "2026-11-07 21:00:00", name="Завтра")     # 00:00 08.11
            data, n_clients, n_orders = await report.build_daily_xlsx(DAY)
            names = [r[1] for r in rows(data, "Новые клиенты")[1:]]
            self.assertEqual(sorted(names), ["Конец", "Начало"])
            self.assertEqual((n_clients, n_orders), (2, 0))
        asyncio.run(go())

    def test_orders_sheet(self):
        async def go():
            await reset()
            uid = await add_user(1, "2026-11-07 09:00:00")
            svc = await db.fetch_one("SELECT id, name FROM services LIMIT 1")
            await db.execute("INSERT INTO orders (user_id, service_id, status, start_at, address, price_total, created_at) "
                             "VALUES (?,?,?,?,?,?,?)", (uid, svc["id"], "created", "2026-11-09 10:00:00", "Ленина 1", 1500,
                                                         "2026-11-07 10:00:00"))
            data, _, n_orders = await report.build_daily_xlsx(DAY)
            self.assertEqual(n_orders, 1)
            r = rows(data, "Заказы")
            self.assertEqual(r[0][0], "№")
            self.assertEqual(r[1][-1], 1500)
            self.assertIn(svc["name"], r[1])
        asyncio.run(go())

    def test_formula_injection_is_neutralised(self):
        async def go():
            await reset()
            await add_user(1, "2026-11-07 09:00:00", name='=HYPERLINK("http://x","y")', username="@evil", phone="+7900")
            data, *_ = await report.build_daily_xlsx(DAY)
            for row in rows(data, "Новые клиенты")[1:]:
                for v in row:
                    if isinstance(v, str):
                        self.assertNotIn(v[:1], ("=", "+", "-", "@"), v)
        asyncio.run(go())

    def test_erased_client_is_dashes_not_crash(self):
        async def go():
            await reset()
            await db.execute("INSERT INTO users (tg_id, created_at) VALUES (5, '2026-11-07 09:00:00')")
            data, n, _ = await report.build_daily_xlsx(DAY)
            self.assertEqual(n, 1)
            self.assertIn("—", rows(data, "Новые клиенты")[1])
        asyncio.run(go())

    def test_empty_day(self):
        async def go():
            await reset()
            _, n, o = await report.build_daily_xlsx(DAY)
            self.assertEqual((n, o), (0, 0))
        asyncio.run(go())


class Cell(unittest.TestCase):
    def test_safe(self):
        self.assertEqual(report.safe("=1+1"), "'=1+1")
        self.assertEqual(report.safe("Анна"), "Анна")
        self.assertEqual(report.safe(None), "—")
        self.assertEqual(report.safe(5), 5)
        self.assertEqual(report.safe("+7900"), "'+7900")


if __name__ == "__main__":
    unittest.main()
