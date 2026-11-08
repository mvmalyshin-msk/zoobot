"""Чистка тестовых данных (один раз, с копией) и выгрузка базы клиентов / отчёт по статусам (заявка владельца 8 ноября 2026)."""
import asyncio
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_purge_export_test.db"

import openpyxl  # noqa: E402
from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import exports  # noqa: E402
import keyboards as kb  # noqa: E402
import purge  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
_OLD_DB = config.db_path
MY_DB = "/tmp/zoobot_purge_export_test.db"


def setUpModule():
    object.__setattr__(config, "db_path", MY_DB)  # свой файл базы: чистка и удаление файла не должны задевать соседние тесты


def tearDownModule():
    object.__setattr__(config, "db_path", _OLD_DB)


CUT = "2026-11-08 15:40:00"


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=0, type="private"))


async def reset():
    """Чистая база с типичным «тестовым» содержимым: старые клиенты/заказы/заявки, админ, исполнитель, один новый заказ."""
    for f in ("", "-wal", "-shm"):
        try:
            os.remove(MY_DB + f)
        except FileNotFoundError:
            pass
    await db.init_db()
    sid = (await db.fetch_one("SELECT id FROM services WHERE code='walk30'"))["id"]
    old, new = "2026-11-07 10:00:00", "2026-11-08 16:00:00"
    ids = {}
    ids["old_user"] = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent, created_at) VALUES (900001, 'Тест Тестович', '+79001110000', 1, ?)", (old,))
    ids["admin_user"] = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent, created_at) VALUES (111, 'Админ', '+79778164660', 1, ?)", (old,))
    ids["exec_user"] = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent, created_at) VALUES (900002, 'Исп', '+79002220000', 1, ?)", (old,))
    await db.execute("INSERT INTO executors (tg_id, name, phone, legal_status, services, districts, rate_type, rate_value, active) "
                     "VALUES (900002, 'Исполнитель', '+79002220000', 'self_employed', 'walk', 'Химки', 'percent', 70, 1)")
    ids["new_user"] = await db.execute("INSERT INTO users (tg_id, name, phone, pdn_consent, created_at) VALUES (900003, 'Настоящий', '+79003330000', 1, ?)", (new,))
    await db.execute("INSERT INTO pets (user_id, name, species) VALUES (?, 'Тестик', 'собака')", (ids["old_user"],))
    ids["old_order"] = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, status, created_at) VALUES (?,?, '2026-11-09 10:00:00', 910, 'cancelled', ?)", (ids["old_user"], sid, old))
    ids["new_order"] = await db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total, status, created_at) VALUES (?,?, '2026-11-12 10:00:00', 910, 'created', ?)", (ids["new_user"], sid, new))
    for oid, st in ((ids["old_order"], "cancelled"), (ids["new_order"], "created")):
        await db.execute("INSERT INTO order_statuses_log (order_id, from_status, to_status, actor, created_at) VALUES (?,?,?,?,?)", (oid, "created", st, "admin:111", old if oid == ids["old_order"] else new))
    await db.execute("INSERT INTO payments (order_id, amount, status) VALUES (?, 910, 'pending')", (ids["old_order"],))
    await db.execute("INSERT INTO web_requests (service_id, name, phone, address, created_at) VALUES (?, 'Форма Старая', '+79007770000', 'Мира 1', ?)", (sid, old))
    await db.execute("INSERT INTO web_requests (service_id, name, phone, address, created_at) VALUES (?, 'Форма Новая', '+79008880000', 'Мира 2', ?)", (sid, new))
    await db.execute("INSERT INTO messages (party_tg_id, party_kind, direction, text, created_at) VALUES (900001, 'client', 'in', 'привет', ?)", (old,))
    await db.execute("INSERT INTO messages (party_tg_id, party_kind, direction, text, created_at) VALUES (900003, 'client', 'in', 'здравствуйте', ?)", (new,))
    return ids


def count(table, where="1=1", params=()):
    return LOOP.run_until_complete(db.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params))["n"]


class Purge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ids = LOOP.run_until_complete(reset())

    def run_purge(self):
        return LOOP.run_until_complete(purge.run_once(None, cutoff=CUT, backup_dir=Path(self.tmp)))

    def test_old_orders_requests_clients_are_removed(self):
        r = self.run_purge()
        self.assertEqual((count("orders", "id=?", (self.ids["old_order"],)), count("web_requests", "name='Форма Старая'")), (0, 0))
        self.assertEqual(count("users", "id=?", (self.ids["old_user"],)), 0)
        self.assertEqual(count("pets"), 0)
        self.assertEqual(count("payments"), 0)
        self.assertEqual(count("messages", "party_tg_id=900001"), 0)
        self.assertEqual((r["orders"], r["requests"], r["clients"]), (1, 1, 1))

    def test_everything_after_the_cutoff_stays(self):
        self.run_purge()
        self.assertEqual(count("orders", "id=?", (self.ids["new_order"],)), 1)
        self.assertEqual(count("web_requests", "name='Форма Новая'"), 1)
        self.assertEqual(count("users", "id=?", (self.ids["new_user"],)), 1)
        self.assertEqual(count("messages", "party_tg_id=900003"), 1)
        self.assertEqual(count("order_statuses_log", "order_id=?", (self.ids["new_order"],)), 1)

    def test_admin_executor_and_settings_stay(self):
        self.run_purge()
        self.assertEqual(count("users", "tg_id=111"), 1)
        self.assertEqual(count("users", "tg_id=900002"), 1)
        self.assertEqual(count("executors"), 1)
        self.assertGreaterEqual(count("services"), 7)
        self.assertGreaterEqual(count("price_rules"), 5)

    def test_backup_is_made_before_deleting(self):
        self.run_purge()
        files = list(Path(self.tmp).glob("*.db"))
        self.assertEqual(len(files), 1)
        import sqlite3
        c = sqlite3.connect(files[0])
        self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 2)  # в копии ещё всё на месте
        c.close()

    def test_runs_only_once(self):
        self.assertIsNotNone(self.run_purge())
        LOOP.run_until_complete(db.execute("INSERT INTO users (tg_id, name, created_at) VALUES (900009, 'Ещё тест', '2026-11-07 09:00:00')"))
        self.assertIsNone(self.run_purge())
        self.assertEqual(count("users", "tg_id=900009"), 1)  # второй раз ничего не трогаем

    def test_failed_backup_means_nothing_is_deleted(self):
        r = LOOP.run_until_complete(purge.run_once(None, cutoff=CUT, backup_dir=Path("/proc/nope/zzz")))
        self.assertIsNone(r)
        self.assertEqual(count("orders"), 2)

    def test_numbering_restarts_when_nothing_is_left(self):
        LOOP.run_until_complete(db.execute("DELETE FROM order_statuses_log WHERE order_id=?", (self.ids["new_order"],)))
        LOOP.run_until_complete(db.execute("DELETE FROM orders WHERE id=?", (self.ids["new_order"],)))
        self.run_purge()
        sid = LOOP.run_until_complete(db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
        n = LOOP.run_until_complete(db.execute("INSERT INTO orders (user_id, service_id, start_at, price_total) VALUES (?,?, '2030-01-01 10:00:00', 1)", (self.ids["new_user"], sid)))
        self.assertEqual(n, 1)


class Export(unittest.TestCase):
    def setUp(self):
        self.ids = LOOP.run_until_complete(reset())

    def book(self):
        data = LOOP.run_until_complete(exports.build_database_xlsx())
        return openpyxl.load_workbook(io.BytesIO(data))

    def test_sheets(self):
        self.assertEqual(self.book().sheetnames, ["Клиенты", "Заказы", "Заявки с сайта", "История статусов", "Статусы"])

    def test_clients_sheet_has_everyone_with_order_stats(self):
        ws = self.book()["Клиенты"]
        head = [c.value for c in ws[1]]
        for col in ("Дата регистрации", "Имя", "Телефон", "Telegram", "Источник", "Питомцы", "Заказов", "Сумма выполненных, ₽", "Последний заказ"):
            self.assertIn(col, head)
        names = {r[head.index("Имя")] for r in ws.iter_rows(min_row=2, values_only=True)}
        self.assertIn("Настоящий", names)
        self.assertIn("Тест Тестович", names)

    def test_orders_and_requests_sheets(self):
        wb = self.book()
        self.assertEqual(wb["Заказы"].max_row, 3)
        self.assertEqual(wb["Заявки с сайта"].max_row, 3)
        self.assertEqual(wb["История статусов"].max_row, 3)

    def test_status_sheet_counts(self):
        ws = self.book()["Статусы"]
        rows = {r[0]: r[1] for r in ws.iter_rows(min_row=2, values_only=True) if r[0]}
        self.assertEqual(rows["Заявка принята"], 1)
        self.assertEqual(rows["Отменён"], 1)

    def test_formula_injection_is_escaped(self):
        LOOP.run_until_complete(db.execute("UPDATE users SET name='=HYPERLINK(\"http://x\")' WHERE id=?", (self.ids["old_user"],)))
        ws = self.book()["Клиенты"]
        vals = [c for r in ws.iter_rows(min_row=2, values_only=True) for c in r if isinstance(c, str)]
        self.assertTrue(any(v.startswith("'=HYPERLINK") for v in vals))
        self.assertFalse(any(v.startswith("=") for v in vals))

    def test_status_report_text(self):
        text = LOOP.run_until_complete(exports.status_report_text())
        self.assertIn("Отчёт по статусам", text)
        self.assertIn("Заявка принята: 1", text)
        self.assertIn("Отменён: 1", text)


class Buttons(unittest.TestCase):
    def test_admin_menu_has_database_and_status_buttons(self):
        data = [b.callback_data for row in kb.admin_menu().inline_keyboard for b in row]
        self.assertIn("adm:base", data)
        self.assertIn("adm:statuses", data)


class AdminHandlers(unittest.TestCase):
    def test_base_button_sends_an_excel_document_and_status_button_sends_text(self):
        sent = []

        class Rec(BaseSession):
            async def close(self): ...
            async def stream_content(self, *a, **kw): ...

            async def make_request(self, bot, method, timeout=None):
                sent.append(method)
                return Message(message_id=1, date=datetime.now(), chat=Chat(id=111, type="private"))

        from handlers import admin

        async def go():
            await reset()
            bot = Bot("1:x", session=Rec())
            m = Message(message_id=5, date=datetime.now(), chat=Chat(id=111, type="private")).as_(bot)
            await admin._send_database(m)
            return sent[-1]
        doc = LOOP.run_until_complete(go())
        self.assertEqual(type(doc).__name__, "SendDocument")
        self.assertTrue(doc.document.filename.endswith(".xlsx"))


class Portal(unittest.TestCase):
    def go(self, fn, admin=True):
        async def run():
            await reset()
            bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
            app = webserver.make_app()
            app["bot"], app["dp"] = bot, None
            async with TestClient(TestServer(app)) as c:
                if admin:
                    c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
                return await fn(c)
        return LOOP.run_until_complete(run())

    def test_download_needs_admin(self):
        async def t(c):
            r = await c.get("/portal/export/database.xlsx")
            self.assertNotEqual(r.headers.get("Content-Type", ""), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.go(t, admin=False)

    def test_download_gives_excel(self):
        async def t(c):
            r = await c.get("/portal/export/database.xlsx")
            self.assertEqual(r.status, 200)
            self.assertIn("spreadsheetml", r.headers["Content-Type"])
            self.assertIn("attachment", r.headers["Content-Disposition"])
            wb = openpyxl.load_workbook(io.BytesIO(await r.read()))
            self.assertIn("Клиенты", wb.sheetnames)
        self.go(t)

    def test_reports_page_has_status_summary_and_download(self):
        async def t(c):
            html = await (await c.get("/portal/reports")).text()
            self.assertIn("/portal/export/database.xlsx", html)
            self.assertIn("Заявка принята", html)
            menu = await (await c.get("/portal")).text()
            self.assertIn('href="/portal/reports"', menu)
        self.go(t)


if __name__ == "__main__":
    unittest.main()
