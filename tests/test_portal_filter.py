"""Заявки: по умолчанию только новые и в работе, а все переключатели спрятаны за кнопкой «Фильтр».
Клиенты: в списке все, кто писал в чат или оставил заявку, а не только давшие согласие или сделавшие заказ."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_portal_filter_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402


class PortalFilterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        await db.init_db()
        await db.execute("DELETE FROM web_requests WHERE phone LIKE '+790011100%' OR phone LIKE '+790022200%'")
        await db.execute("DELETE FROM messages WHERE party_tg_id BETWEEN -504 AND -501")
        await db.execute("DELETE FROM users WHERE tg_id BETWEEN -504 AND -501")
        sid = (await db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
        await db.execute("INSERT INTO web_requests (service_id, name, phone, address, status) VALUES (?, 'Новая Нина', '+79001110001', 'Мира 1', 'new')", (sid,))
        await db.execute("INSERT INTO web_requests (service_id, name, phone, address, status) VALUES (?, 'Работа Рита', '+79001110002', 'Мира 2', 'work')", (sid,))
        await db.execute("INSERT INTO web_requests (service_id, name, phone, address, status) VALUES (?, 'Готов Гена', '+79001110003', 'Мира 3', 'done')", (sid,))
        self.client = TestClient(TestServer(webserver.make_app()))
        await self.client.start_server()
        self.h = {"Cookie": f"{webchat.ADMIN_COOKIE}={await webchat.new_session(111)}"}

    async def asyncTearDown(self):
        await self.client.close()

    async def get(self, path):
        return await (await self.client.get(path, headers=self.h)).text()

    async def test_default_shows_only_new_and_in_work(self):
        t = await self.get("/portal")
        self.assertIn("Новая Нина", t)
        self.assertIn("Работа Рита", t)
        self.assertNotIn("Готов Гена", t)

    async def test_filters_hidden_behind_button(self):
        t = await self.get("/portal")
        i = t.index("<summary", t.index('class="filter"'))
        self.assertIn("Фильтр", t[i:i + 80])
        details = t[t.rindex("<details", 0, i):t.index("</details>", i)]
        for must in ("Новые", "В работе", "Все", "Все источники", "Форма на сайте", "Чат на сайте", "Telegram"):
            self.assertIn(must, details)
        outside = t.replace(details, "")
        self.assertNotIn("Все источники", outside)

    async def test_filter_still_works_and_is_open_when_chosen(self):
        t = await self.get("/portal?f=all&src=form")
        self.assertIn("Готов Гена", t)
        self.assertIn("<details class=\"filter\" open", t)

    async def test_clients_list_includes_chat_guests_and_request_people(self):
        await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (-501, 'Гость Гоша', '+79002220001')")
        await db.execute("INSERT INTO messages (party_tg_id, party_kind, direction, text) VALUES (-501, 'client', 'in', 'Добрый день, нужен выгул')")
        await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (-502, 'Молчун Миша', '+79002220002')")      # ничего не делал
        t = await self.get("/portal/clients")
        self.assertIn("Гость Гоша", t)
        self.assertIn("Добрый день", t)                                   # последнее сообщение видно сразу
        self.assertNotIn("Молчун Миша", t)
        self.assertIn('href="/portal/chat/-501"', t)

    async def test_clients_has_link_to_request(self):
        await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (-503, 'Заявка Зоя', '+79002220003')")
        await db.execute("INSERT INTO web_requests (name, phone, address, web_uid) VALUES ('Заявка Зоя', '+79002220003', 'Мира 5', -503)")
        t = await self.get("/portal/clients")
        self.assertIn("Заявка Зоя", t)

    async def test_clients_page_survives_odd_rows(self):
        await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (-504, NULL, NULL)")
        await db.execute("INSERT INTO messages (party_tg_id, party_kind, direction, text) VALUES (-504, 'client', 'in', '<b>x</b>')")
        r = await self.client.get("/portal/clients", headers=self.h)
        t = await r.text()
        self.assertEqual(r.status, 200)
        self.assertNotIn("<b>x</b>", t)
        self.assertIn("Без имени", t)


if __name__ == "__main__":
    unittest.main()
