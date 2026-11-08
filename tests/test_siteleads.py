"""Заявки с сайтов конструктора (workbot) падают в общий список «Заявки» портала."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_siteleads_test.db"
os.environ["SITE_LEADS_KEY"] = "test-key-123456789012345678901234"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import db  # noqa: E402
import orders  # noqa: E402
import portal  # noqa: E402
import siteleads  # noqa: E402
import webserver  # noqa: E402

KEY = {"X-Site-Key": os.environ["SITE_LEADS_KEY"]}
LEAD = {"site": "zapis-vygula-sobak", "answers": [
    ["Ваше имя", "Анна"], ["Контактный телефон", "8 900 777-88-99"],
    ["Информация о питомце (имя, возраст, вес, особенности)", "Дымок, 3 года, 20 кг"],
    ["Выберите услугу", "зоотакси"]]}


class ParseTest(unittest.TestCase):
    def test_fields_by_label(self):
        c = siteleads.parse(LEAD)
        self.assertEqual(c["name"], "Анна")
        self.assertEqual(c["phone"], "+79007778899")
        self.assertIn("Дымок", c["pet"])
        self.assertIn("зоотакси", c["comment"])

    def test_rejects_garbage(self):
        for bad in (None, [], {}, {"answers": "x"}, {"site": "a", "answers": []},
                    {"site": "a", "answers": [["x", "y"]] * 100}):
            self.assertIsNone(siteleads.parse(bad))

    def test_no_phone_still_accepted_with_raw_text(self):
        c = siteleads.parse({"site": "s", "answers": [["Ваше имя", "Анна"], ["Контактный телефон", "позвоните"]]})
        self.assertEqual(c["phone"], "позвоните")


class HttpTest(unittest.TestCase):
    def run_app(self, coro):
        async def go():
            await db.init_db()
            sent = []

            async def fake(bot, text, reply_markup=None):
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

    def test_requires_key(self):
        async def case(c, sent):
            self.assertEqual((await c.post("/api/site-lead", json=LEAD)).status, 403)
            self.assertEqual((await c.post("/api/site-lead", json=LEAD, headers={"X-Site-Key": "wrong"})).status, 403)
            self.assertEqual(sent, [])
        self.run_app(case)

    def test_disabled_without_server_key(self):
        async def case(c, sent):
            old = os.environ.pop("SITE_LEADS_KEY")
            try:
                self.assertEqual((await c.post("/api/site-lead", json=LEAD, headers=KEY)).status, 403)
            finally:
                os.environ["SITE_LEADS_KEY"] = old
        self.run_app(case)

    def test_saved_notified_and_listed_in_portal(self):
        async def case(c, sent):
            r = await c.post("/api/site-lead", json=LEAD, headers=KEY)
            self.assertEqual(r.status, 200)
            rid = (await r.json())["id"]
            row = await db.fetch_one("SELECT * FROM web_requests WHERE id=?", (rid,))
            self.assertEqual(row["name"], "Анна")
            self.assertEqual(row["phone"], "+79007778899")
            self.assertIn("zapis-vygula-sobak", row["comment"])
            self.assertTrue(any("№%d" % rid in t and "Анна" in t for t in sent))
            rows = await portal.all_requests("new", "all")
            self.assertTrue(any(x["kind"] == "form" and x["id"] == rid for x in rows))
            self.assertIn("Сайт", portal._row_html([x for x in rows if x["id"] == rid][0]))
        self.run_app(case)

    def test_bad_body(self):
        async def case(c, sent):
            r = await c.post("/api/site-lead", data="не json", headers=KEY)
            self.assertEqual(r.status, 400)
            r = await c.post("/api/site-lead", json={"site": "a", "answers": []}, headers=KEY)
            self.assertEqual(r.status, 400)
        self.run_app(case)


if __name__ == "__main__":
    unittest.main()
