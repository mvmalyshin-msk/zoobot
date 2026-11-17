"""Исполнители в веб-админке: форма (организация / физлицо), проверки, создание, правка, удаление, страницы портала."""
import asyncio
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_executors_web_test.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from multidict import MultiDict  # noqa: E402

import db  # noqa: E402
import executors_web as ew  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()


def run(coro):
    return LOOP.run_until_complete(coro)


def form(**kw):
    base = {"kind": "person", "name": "Иван Петров", "phone": "8 (900) 111-22-33", "links": "", "status": "1", "tax": "self_employed",
            "rate_type": "percent", "rate_value": "70"}
    base.update(kw)
    md = MultiDict()
    services = base.pop("services", ["walk"])
    for k, v in base.items():
        md.add(k, v)
    for s in services:
        md.add("services", s)
    return md


class Parse(unittest.TestCase):
    def test_person_ok(self):
        c, e = ew.parse(form())
        self.assertEqual(e, {})
        self.assertEqual(c["name"], "Иван Петров")
        self.assertEqual(c["phone"], "+79001112233")
        self.assertEqual(c["legal_status"], "self_employed")
        self.assertEqual(c["active"], 1)
        self.assertEqual(c["services"], "walk")
        self.assertEqual((c["rate_type"], c["rate_value"]), ("percent", 70.0))

    def test_organization_is_legal_entity(self):
        c, e = ew.parse(form(kind="org", name='ООО "Лапы"', tax="ip"))
        self.assertEqual(e, {})
        self.assertEqual(c["legal_status"], "legal")  # налоговый статус физлица к организации не относится

    def test_kind_is_derived_back(self):
        self.assertEqual(ew.kind_of({"legal_status": "legal"}), "org")
        for st in ("self_employed", "ip", "individual"):
            self.assertEqual(ew.kind_of({"legal_status": st}), "person")

    def test_required_fields(self):
        _, e = ew.parse(form(kind="x", name=" ", phone="123", services=[]))
        self.assertEqual(set(e), {"kind", "name", "phone", "services"})

    def test_labels_depend_on_kind(self):
        self.assertEqual(ew.name_label("org"), "Название организации")
        self.assertEqual(ew.name_label("person"), "ФИО")

    def test_inactive_status(self):
        c, _ = ew.parse(form(status="0"))
        self.assertEqual(c["active"], 0)

    def test_unknown_service_and_long_name_rejected(self):
        _, e = ew.parse(form(services=["walk", "hack"], name="я" * 200))
        self.assertIn("services", e)
        self.assertIn("name", e)

    def test_links_are_normalized(self):
        c, e = ew.parse(form(links="@ivan_p\nhttps://vk.com/ivan\nt.me/ivan2"))
        self.assertEqual(e, {})
        self.assertEqual(c["links"].split("\n"), ["https://t.me/ivan_p", "https://vk.com/ivan", "https://t.me/ivan2"])

    def test_bad_links_rejected(self):
        for bad in ("javascript:alert(1)", "просто текст", "ftp://x.ru", "https://", "@a"):
            _, e = ew.parse(form(links=bad))
            self.assertIn("links", e, bad)

    def test_too_many_links(self):
        _, e = ew.parse(form(links="\n".join(f"https://vk.com/u{i}" for i in range(8))))
        self.assertIn("links", e)

    def test_individual_needs_birth_year_and_age(self):
        _, e = ew.parse(form(tax="individual"))
        self.assertIn("birth_year", e)
        _, e = ew.parse(form(tax="individual", birth_year="2020"))
        self.assertIn("birth_year", e)  # младше 14
        _, e = ew.parse(form(tax="individual", birth_year="2010"))  # 14–17: нужно согласие родителей
        self.assertIn("parent_consent", e)
        c, e = ew.parse(form(tax="individual", birth_year="2010", parent_consent="on"))
        self.assertEqual(e, {})
        self.assertEqual(c["parent_consent"], 1)

    def test_rate_checks(self):
        for rt, rv in (("percent", "0"), ("percent", "150"), ("fixed", "-5"), ("percent", "abc"), ("weird", "5")):
            _, e = ew.parse(form(rate_type=rt, rate_value=rv))
            self.assertIn("rate", e, (rt, rv))
        c, e = ew.parse(form(rate_type="fixed", rate_value="500,5"))
        self.assertEqual((c["rate_type"], c["rate_value"]), ("fixed", 500.5))

    def test_tg_id_must_be_number(self):
        _, e = ew.parse(form(tg_id="abc"))
        self.assertIn("tg_id", e)
        c, e = ew.parse(form(tg_id="12345"))
        self.assertEqual(c["tg_id"], 12345)


class Storage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.path.exists(os.environ["DB_PATH"]):
            os.remove(os.environ["DB_PATH"])
        run(db.init_db())

    def test_create_read_update(self):
        c, _ = ew.parse(form(links="@ivan_p", districts="Химки", tg_id="777001"))
        eid = run(ew.create(c))
        row = run(db.fetch_one("SELECT * FROM executors WHERE id=?", (eid,)))
        self.assertEqual((row["name"], row["phone"], row["links"], row["tg_id"], row["active"]),
                         ("Иван Петров", "+79001112233", "https://t.me/ivan_p", 777001, 1))
        c2, _ = ew.parse(form(name="Иван П.", status="0", links=""))
        run(ew.update(eid, c2))
        row = run(db.fetch_one("SELECT * FROM executors WHERE id=?", (eid,)))
        self.assertEqual((row["name"], row["active"], row["links"]), ("Иван П.", 0, None))

    def test_duplicate_telegram_id_is_reported(self):
        c, _ = ew.parse(form(tg_id="777002"))
        run(ew.create(c))
        with self.assertRaises(ew.Duplicate):
            run(ew.create(c))

    def test_delete_without_history(self):
        c, _ = ew.parse(form(tg_id="777003"))
        eid = run(ew.create(c))
        code, _ = run(ew.delete(eid))
        self.assertEqual(code, "deleted")
        self.assertIsNone(run(db.fetch_one("SELECT 1 FROM executors WHERE id=?", (eid,))))

    def test_delete_with_orders_is_blocked_and_suggests_deactivation(self):
        c, _ = ew.parse(form(tg_id="777004"))
        eid = run(ew.create(c))
        uid = run(db.execute("INSERT INTO users (tg_id, name) VALUES (?,?)", (777005, "К")))
        sid = run(db.fetch_one("SELECT id FROM services LIMIT 1"))["id"]
        run(db.execute("INSERT INTO orders (user_id, service_id, status, start_at, quantity, address, price_total, executor_id) "
                       "VALUES (?,?,?,?,?,?,?,?)", (uid, sid, "completed", "2026-12-01 10:00:00", 1, "Мира 1", 900, eid)))
        code, msg = run(ew.delete(eid))
        self.assertEqual(code, "blocked")
        self.assertIn("Неактивен", msg)
        self.assertIsNotNone(run(db.fetch_one("SELECT 1 FROM executors WHERE id=?", (eid,))))

    def test_delete_missing(self):
        self.assertEqual(run(ew.delete(99999))[0], "missing")


class Portal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run(db.init_db())

    def with_admin(self, fn):
        async def go():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
                return await fn(c)
        return run(go())

    def test_list_has_add_button_and_no_bot_hint(self):
        async def fn(c):
            return await (await c.get("/portal/executors")).text()
        html = self.with_admin(fn)
        self.assertIn('href="/portal/executors/new"', html)
        self.assertNotIn("команда /add_executor", html)

    def test_new_form_fields(self):
        async def fn(c):
            return await (await c.get("/portal/executors/new")).text()
        html = self.with_admin(fn)
        for needle in ('name="kind"', "Организация", "Физлицо", "ФИО", 'name="phone"', 'name="links"', 'name="status"',
                       'name="services"', "Активен", "Неактивен"):
            self.assertIn(needle, html, needle)
        self.assertIn('data-label-org="Название организации"', html)

    def test_create_through_form_then_edit_and_delete(self):
        async def fn(c):
            data = form(name="Мария Л.", tg_id="")
            r = await c.post("/portal/executors/new", data=data, allow_redirects=False)
            self.assertEqual(r.status, 303)
            row = await db.fetch_one("SELECT * FROM executors WHERE name='Мария Л.'")
            self.assertIsNotNone(row)
            page = await (await c.get(f"/portal/executors/{row['id']}")).text()
            self.assertIn("Мария Л.", page)
            self.assertIn("Вы уверены, что хотите удалить исполнителя?", page)
            r = await c.post(f"/portal/executors/{row['id']}", data=form(name="Мария Лебедева", status="0", tg_id=""), allow_redirects=False)
            self.assertEqual(r.status, 303)
            row2 = await db.fetch_one("SELECT * FROM executors WHERE id=?", (row["id"],))
            self.assertEqual((row2["name"], row2["active"]), ("Мария Лебедева", 0))
            r = await c.post(f"/portal/executors/{row['id']}/delete", allow_redirects=False)
            self.assertEqual(r.status, 303)
            self.assertIsNone(await db.fetch_one("SELECT 1 FROM executors WHERE id=?", (row["id"],)))
        self.with_admin(fn)

    def test_invalid_form_shows_errors_and_keeps_values(self):
        async def fn(c):
            r = await c.post("/portal/executors/new", data=form(name="Анна", phone="12"), allow_redirects=False)
            self.assertEqual(r.status, 200)
            html = await r.text()
            self.assertIn("телефон", html.lower())
            self.assertIn('value="Анна"', html)
            self.assertIsNone(await db.fetch_one("SELECT 1 FROM executors WHERE name='Анна'"))
        self.with_admin(fn)

    def test_posts_need_admin_and_same_origin(self):
        async def fn(c):
            r = await c.post("/portal/executors/new", data=form(name="Злой"), headers={"Origin": "https://evil.example"}, allow_redirects=False)
            self.assertEqual(r.status, 403)
            self.assertIsNone(await db.fetch_one("SELECT 1 FROM executors WHERE name='Злой'"))
        self.with_admin(fn)

        async def anon():
            app = webserver.make_app()
            app["bot"] = None
            async with TestClient(TestServer(app)) as c:
                return (await c.post("/portal/executors/new", data=form(name="Аноним"), allow_redirects=False)).status
        self.assertEqual(run(anon()), 403)

    def test_unknown_executor_is_404(self):
        async def fn(c):
            return (await c.get("/portal/executors/99999")).status, (await c.get("/portal/executors/abc")).status
        self.assertEqual(self.with_admin(fn), (404, 404))

    def test_links_are_rendered_safely(self):
        async def fn(c):
            c1, _ = ew.parse(form(name="Ссылочный", links="https://vk.com/a?x=1&y=2", tg_id="777010"))
            eid = await ew.create(c1)
            return await (await c.get("/portal/executors")).text(), await (await c.get(f"/portal/executors/{eid}")).text()
        lst, one = self.with_admin(fn)
        self.assertIn('rel="noopener noreferrer"', lst + one)
        self.assertIn("x=1&amp;y=2", lst + one)


if __name__ == "__main__":
    unittest.main()
