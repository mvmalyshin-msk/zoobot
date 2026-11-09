"""Аналитика простым языком: источники, устройства, шаги формы, сводка; устройства владельца вычищаются."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_analytics_v2_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

import analytics  # noqa: E402
from tests.test_analytics import LOOP, post, rows, run, seed  # noqa: E402


class SourceLabels(unittest.TestCase):
    def test_known_sources_get_plain_names(self):
        f = analytics.source_label
        self.assertEqual(f(""), "Напрямую (адрес или закладка)")
        self.assertEqual(f(None), "Напрямую (адрес или закладка)")
        self.assertEqual(f("yandex.ru"), "Яндекс (поиск)")
        self.assertEqual(f("ya.ru"), "Яндекс (поиск)")
        self.assertEqual(f("google.com"), "Google (поиск)")
        self.assertEqual(f("t.me"), "Telegram")
        self.assertEqual(f("m.vk.com"), "ВКонтакте")
        self.assertEqual(f("avito"), "Авито")
        self.assertEqual(f("some-blog.ru"), "some-blog.ru")


class Collected(unittest.TestCase):
    def collect(self):
        async def t():
            await seed()
            return await analytics.collect("web", "7")
        return LOOP.run_until_complete(t())

    def test_sources_use_plain_names(self):
        names = {r["name"]: r["n"] for r in self.collect()["sources"]}
        self.assertEqual(names.get("Яндекс (поиск)"), 1)
        self.assertEqual(names.get("Напрямую (адрес или закладка)"), 1)

    def test_devices_have_plain_names_and_share(self):
        d = {r["name"]: r for r in self.collect()["devices"]}
        self.assertEqual(d["Телефон"]["n"], 1)
        self.assertEqual(d["Компьютер"]["pct"], 50)

    def test_form_steps_follow_the_form_order(self):
        steps = self.collect()["form_steps"]
        names = [n for n, _ in steps]
        self.assertEqual(names[0], "Открыли форму заявки")
        self.assertEqual(names[-1], "Отправили заявку")
        self.assertLess(names.index("Услуга"), names.index("Телефон"))
        counts = dict(steps)
        self.assertEqual(counts["Открыли форму заявки"], 1)
        self.assertEqual(counts["Телефон"], 1)
        self.assertEqual(counts["Адрес"], 0)

    def test_summary_says_it_in_words(self):
        text = " ".join(self.collect()["summary"])
        self.assertIn("2", text)
        self.assertIn("Яндекс", text)
        self.assertIn("заявк", text)

    def test_rendered_page_has_plain_blocks(self):
        async def t():
            await seed()
            return analytics.render(await analytics.collect("web", "7"))
        html = LOOP.run_until_complete(t())
        for word in ("Коротко", "Откуда пришли", "С каких устройств", "До какого вопроса дошли"):
            self.assertIn(word, html)


class OwnerVisitor(unittest.TestCase):
    def test_marking_a_visitor_id_clears_past_and_blocks_future_events(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/"}], v="ownerphone123456", s="sown123456789abc")
            await post(c, [{"t": "pv", "p": "/"}], v="stranger12345678", s="sstr123456789abc")
            self.assertEqual(len(await rows()), 2)
            import webchat
            c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
            r = await c.post("/portal/api/analytics/own", data=json.dumps({"v": "ownerphone123456"}),
                             headers={"Origin": str(c.make_url("")).rstrip("/"), "Content-Type": "application/json"})
            self.assertEqual(r.status, 200)
            self.assertEqual([x["vid"] for x in await rows()], ["stranger12345678"])
            c.session.cookie_jar.clear()
            await post(c, [{"t": "pv", "p": "/request"}], v="ownerphone123456", s="sown123456789abc")
            self.assertEqual(len(await rows()), 1)
        run(t)

    def test_only_admin_can_mark(self):
        async def t(c):
            r = await c.post("/portal/api/analytics/own", data=json.dumps({"v": "ownerphone123456"}),
                             headers={"Content-Type": "application/json"})
            self.assertIn(r.status, (401, 403))
        run(t)

    def test_portal_pages_send_the_visitor_id(self):
        async def t(c):
            html = await (await c.get("/portal/analytics")).text()
            self.assertIn("/portal/api/analytics/own", html)
            self.assertIn("zc_a", html)
        run(t, admin=True)


class Reset(unittest.TestCase):
    def test_reset_clears_site_events_for_admin_only(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/"}])
            r = await c.post("/portal/api/analytics/reset", headers={"Origin": str(c.make_url("")).rstrip("/")})
            self.assertEqual(r.status, 200)
            self.assertEqual(await rows(), [])
        run(t, admin=True)

    def test_reset_refused_without_admin(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/"}])
            r = await c.post("/portal/api/analytics/reset")
            self.assertIn(r.status, (401, 403))
            self.assertEqual(len(await rows()), 1)
        run(t)


if __name__ == "__main__":
    unittest.main()
