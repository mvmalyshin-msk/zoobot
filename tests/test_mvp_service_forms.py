"""MVP, шаг 2 (проектное предложение 15.11.2026, вариант A): своя заявка для каждой из 4 услуг.

С главной клиент попадает на /request?service=walk|sitter|boarding|taxi и видит:
1) варианты этой услуги карточками с ценой «от … ₽» (самый доступный уже выбран);
2) дату (календарь, не раньше сегодня) и время; у передержки — «с» и «по»;
3) адрес; у зоотакси — «Откуда» и «Куда»;
4) питомца и контакты.
Двойное нажатие «Отправить» не создаёт вторую заявку."""
import asyncio
import os
import re
import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_mvp_service_forms_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import pricing  # noqa: E402
import webform  # noqa: E402
import webserver  # noqa: E402
from config import config  # noqa: E402

TODAY = datetime.now(config.tz).date()
TOMORROW = (TODAY + timedelta(days=1)).isoformat()
CONTACT = {"name": "Анна", "phone": "8 900 777-88-99", "pet": "Дымок, кот", "comment": "", "consent": "on", "site": ""}


def run_app(coro):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM web_requests")
        webform.RATE.clear()
        webform.SEEN.clear()
        sent = []

        async def fake(bot, text, reply_markup=None, **kw):
            sent.append(text)
        orig = orders.notify_admins
        orders.notify_admins = fake
        try:
            app = webserver.make_app()
            app["bot"] = object()
            async with TestClient(TestServer(app)) as c:
                return await coro(c, sent)
        finally:
            orders.notify_admins = orig
    return asyncio.run(go())


async def variants(cat):
    items = await pricing.with_client_price(await db.fetch_all("SELECT * FROM services WHERE active=1"))
    return sorted((s for s in items if s["category"] == cat), key=lambda s: s["from_price"])


class ServicePage(unittest.TestCase):
    def test_each_service_has_its_own_page_with_variant_cards_and_prices(self):
        async def case(c, sent):
            for cat, title in (("walk", "выгул"), ("sitter", "зооняню"), ("boarding", "передержку"), ("taxi", "зоотакси")):
                t = await (await c.get(f"/request?service={cat}")).text()
                self.assertIn(f"Заявка на {title}", t, cat)
                vs = await variants(cat)
                self.assertTrue(vs)
                self.assertRegex(t, rf'<input type="radio" name="service_id" value="{vs[0]["id"]}" checked>', cat)
                for s in vs:
                    self.assertIn(f'value="{s["id"]}"', t)
                    self.assertIn("от " + webform.rub(s["from_price"]), t)
                for s in [s for x in webform.texts.CATEGORY_ORDER if x != cat for s in await variants(x)]:
                    self.assertNotIn(f'name="service_id" value="{s["id"]}"', t, cat)
                self.assertIn('type="date"', t)
                self.assertIn(f'min="{TODAY.isoformat()}"', t)
                self.assertIn('href="/request"', t)                         # можно выбрать другую услугу
        run_app(case)

    def test_taxi_asks_from_and_to_and_boarding_asks_dates(self):
        async def case(c, sent):
            taxi = await (await c.get("/request?service=taxi")).text()
            self.assertIn(">Откуда<", taxi)
            self.assertIn(">Куда<", taxi)
            self.assertIn('name="dest"', taxi)
            board = await (await c.get("/request?service=boarding")).text()
            self.assertIn('name="when_to"', board)
            walk = await (await c.get("/request?service=walk")).text()
            self.assertNotIn('name="dest"', walk)
            self.assertIn('type="time"', walk)
        run_app(case)

    def test_generic_page_still_offers_all_services(self):
        async def case(c, sent):
            t = await (await c.get("/request")).text()
            for word in ("Выгул", "Зооняня", "Передержка", "Зоотакси"):
                self.assertIn(word, t)
        run_app(case)


class Submit(unittest.TestCase):
    def test_taxi_route_and_date_reach_admin_and_database(self):
        async def case(c, sent):
            svc = (await variants("taxi"))[0]
            r = await c.post("/request", data={**CONTACT, "service": "taxi", "service_id": str(svc["id"]),
                                               "address": "Москва, Ленина 1", "dest": "Химки, ветклиника",
                                               "when_date": TOMORROW, "when_time": "10:30", "nonce": "a1"})
            self.assertEqual(r.status, 200, await r.text())
            row = await db.fetch_one("SELECT * FROM web_requests ORDER BY id DESC LIMIT 1")
            self.assertIn("Москва, Ленина 1", row["address"])
            self.assertIn("Химки, ветклиника", row["address"])
            d = date.fromisoformat(TOMORROW)
            self.assertIn(d.strftime("%d.%m.%Y"), row["when_text"])
            self.assertIn("10:30", row["when_text"])
            self.assertIn("Химки, ветклиника", sent[-1])
        run_app(case)

    def test_boarding_period(self):
        async def case(c, sent):
            svc = (await variants("boarding"))[0]
            to = (TODAY + timedelta(days=5)).isoformat()
            await c.post("/request", data={**CONTACT, "service": "boarding", "service_id": str(svc["id"]),
                                           "address": "Москва, Бутово", "when_date": TOMORROW, "when_to": to,
                                           "nonce": "b1"})
            row = await db.fetch_one("SELECT when_text FROM web_requests ORDER BY id DESC LIMIT 1")
            self.assertEqual(row["when_text"], f"с {date.fromisoformat(TOMORROW):%d.%m.%Y} по {date.fromisoformat(to):%d.%m.%Y}")
        run_app(case)

    def test_errors_keep_the_service_page(self):
        async def case(c, sent):
            svc = (await variants("taxi"))[0]
            r = await c.post("/request", data={**CONTACT, "service": "taxi", "service_id": str(svc["id"]),
                                               "address": "Москва", "dest": "", "when_date": "2000-01-01",
                                               "nonce": "c1"})
            self.assertEqual(r.status, 400)
            t = await r.text()
            self.assertIn("Заявка на зоотакси", t)
            self.assertIn("куда отвезти", t)                                # ошибка про пустое «Куда»
            self.assertIn("Дата уже прошла", t)
            self.assertIn('value="Москва"', t)                              # введённое не пропало
            self.assertEqual(sent, [])
        run_app(case)

    def test_double_submit_creates_one_request(self):
        async def case(c, sent):
            svc = (await variants("walk"))[0]
            data = {**CONTACT, "service": "walk", "service_id": str(svc["id"]), "address": "Москва, Тверская 1",
                    "when_date": TOMORROW, "when_time": "09:00", "nonce": "same-click"}
            t1 = await (await c.post("/request", data=data)).text()
            t2 = await (await c.post("/request", data=data)).text()
            n = (await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests"))["n"]
            self.assertEqual(n, 1)
            self.assertEqual(len(sent), 1)
            num = re.search(r"№(\d+)", t1).group(1)
            self.assertIn(f"№{num}", t2)                                   # второй раз — та же заявка
        run_app(case)

    def test_form_has_fresh_nonce(self):
        async def case(c, sent):
            a = await (await c.get("/request?service=walk")).text()
            b = await (await c.get("/request?service=walk")).text()
            na = re.search(r'name="nonce" value="([^"]+)"', a).group(1)
            nb = re.search(r'name="nonce" value="([^"]+)"', b).group(1)
            self.assertNotEqual(na, nb)
        run_app(case)


if __name__ == "__main__":
    unittest.main()
