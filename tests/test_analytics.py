"""Аналитика посещений сайта и бота: сбор событий, подсчёт, страница «Аналитика» в портале администратора."""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_analytics_test.db"
for _s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + _s)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import analytics  # noqa: E402
import brand  # noqa: E402
import db  # noqa: E402
import webchat  # noqa: E402
import webserver  # noqa: E402

LOOP = asyncio.new_event_loop()
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
          "Version/17.5 Mobile/15E148 Safari/604.1")
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
BOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


class Inner(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        return Message(message_id=1, date=datetime.now(), chat=Chat(id=0, type="private"))


def run(fn, admin=False):
    async def go():
        await db.init_db()
        await db.execute("DELETE FROM analytics_events")
        bot = Bot("1:x", session=webchat.WebRouterSession(Inner()))
        app = webserver.make_app()
        app["bot"], app["dp"] = bot, None
        async with TestClient(TestServer(app)) as c:
            if admin:
                c.session.cookie_jar.update_cookies({webchat.ADMIN_COOKIE: await webchat.new_session(111)})
            return await fn(c)
    return LOOP.run_until_complete(go())


async def post(c, events, v="abcdef1234567890", s="s123456789abcdef", ua=IPHONE, raw=None):
    body = raw if raw is not None else json.dumps({"v": v, "s": s, "d": events})
    return await c.post("/api/a", data=body, headers={"User-Agent": ua, "Content-Type": "text/plain"})


async def rows(where="1=1", params=()):
    return await db.fetch_all(f"SELECT * FROM analytics_events WHERE {where} ORDER BY id", params)


class UserAgent(unittest.TestCase):
    def test_iphone(self):
        self.assertEqual(analytics.classify_ua(IPHONE), ("mobile", "Safari", "iOS"))

    def test_desktop(self):
        self.assertEqual(analytics.classify_ua(DESKTOP), ("desktop", "Chrome", "Windows"))

    def test_robots(self):
        self.assertTrue(analytics.is_robot(BOT_UA))
        self.assertTrue(analytics.is_robot(""))
        self.assertFalse(analytics.is_robot(IPHONE))


class Ingest(unittest.TestCase):
    def test_batch_uses_one_database_transaction(self):
        async def t(c):
            with mock.patch.object(db, "transaction", wraps=db.transaction) as transaction:
                r = await post(c, [{"t": "pv", "p": "/"}, {"t": "click", "p": "/", "g": "Заказать"}])
            self.assertEqual(r.status, 204)
            self.assertEqual(transaction.call_count, 1)
            self.assertEqual(len(await rows()), 2)
        run(t)

    def test_page_view_and_click_are_stored(self):
        async def t(c):
            r = await post(c, [{"t": "pv", "p": "/", "r": "yandex.ru", "ti": "Главная"},
                               {"t": "click", "p": "/", "g": "Заказать → /request"},
                               {"t": "scroll", "p": "/", "n": 50},
                               {"t": "leave", "p": "/", "n": 42}])
            self.assertEqual(r.status, 204)
            got = await rows()
            self.assertEqual([x["type"] for x in got], ["pv", "click", "scroll", "leave"])
            self.assertEqual(got[0]["source"], "web")
            self.assertEqual(got[0]["device"], "mobile")
            self.assertEqual(got[0]["ref"], "yandex.ru")
            self.assertEqual(got[1]["target"], "Заказать → /request")
            self.assertEqual(got[2]["value"], 50)
            self.assertEqual(got[3]["value"], 42)
        run(t)

    def test_robots_are_ignored(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/"}], ua=BOT_UA)
            self.assertEqual(await rows(), [])
        run(t)

    def test_portal_pages_are_never_tracked(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/portal/order/5"}, {"t": "pv", "p": "/"}])
            self.assertEqual([x["path"] for x in await rows()], ["/"])
        run(t)

    def test_admin_visits_are_not_counted(self):
        async def t(c):
            await post(c, [{"t": "pv", "p": "/"}])
            self.assertEqual(await rows(), [])
        run(t, admin=True)

    def test_garbage_is_rejected(self):
        async def t(c):
            self.assertEqual((await post(c, [], raw="not json")).status, 400)
            self.assertEqual((await post(c, [], raw="x" * 40000)).status, 413)
            self.assertEqual((await post(c, [{"t": "pv", "p": "/"}], v="bad id!")).status, 400)
            await post(c, [{"t": "hack", "p": "/"}, {"t": "pv", "p": "no-slash"}, {"t": "pv", "p": "/ok"}])
            self.assertEqual([x["path"] for x in await rows()], ["/ok"])
            self.assertEqual(await rows("1=1"), await rows("source='web'"))
        run(t)

    def test_no_personal_data_in_storage(self):
        async def t(c):
            await post(c, [{"t": "focus", "p": "/request", "g": "phone", "value": "+79001234567"}])
            got = await rows()
            self.assertEqual(got[0]["target"], "phone")
            self.assertNotIn("79001234567", json.dumps([dict(x) for x in got], ensure_ascii=False))
        run(t)

    def test_tracker_script_is_served_and_wired(self):
        async def t(c):
            r = await c.get("/a.js")
            self.assertEqual(r.status, 200)
            self.assertIn("javascript", r.headers["Content-Type"])
            js = await r.text()
            for word in ("sendBeacon", "/api/a", "doNotTrack", "pagehide"):
                self.assertIn(word, js)
            for path in ("/", "/request", "/contact", "/policy", "/chat"):
                self.assertIn("/a.js", await (await c.get(path)).text(), path)
        run(t)

    def test_admin_head_has_no_tracker(self):
        self.assertIn("/a.js", brand.HEAD)
        self.assertNotIn("/a.js", brand.HEAD_ADMIN)


class BotEvents(unittest.TestCase):
    def test_command_button_and_message_are_recorded(self):
        async def t():
            await db.init_db()
            await db.execute("DELETE FROM analytics_events")
            await analytics.bot_event(555, "cmd", "/start", state=None)
            await analytics.bot_event(555, "btn", analytics.norm_callback("svc:12"), state="Order:when")
            await analytics.bot_event(555, "msg", "text", state="Order:address")
            return await rows()
        got = LOOP.run_until_complete(t())
        self.assertEqual([(x["type"], x["target"]) for x in got], [("cmd", "/start"), ("btn", "svc"), ("msg", "text")])
        self.assertEqual({x["source"] for x in got}, {"bot"})
        self.assertEqual(len({x["sid"] for x in got}), 1)
        self.assertEqual(got[1]["state"], "Order:when")

    def test_admin_is_not_counted(self):
        async def t():
            await db.init_db()
            await db.execute("DELETE FROM analytics_events")
            await analytics.bot_event(111, "cmd", "/start")
            return await rows()
        self.assertEqual(LOOP.run_until_complete(t()), [])

    def test_new_session_after_30_minutes_of_silence(self):
        async def t():
            await db.init_db()
            await db.execute("DELETE FROM analytics_events")
            analytics._bot_sessions.clear()
            await analytics.bot_event(556, "cmd", "/start")
            await db.execute("UPDATE analytics_events SET ts=datetime('now','-31 minutes')")
            analytics._bot_sessions.clear()
            await analytics.bot_event(556, "btn", "menu:order")
            return await rows()
        got = LOOP.run_until_complete(t())
        self.assertEqual(len({x["sid"] for x in got}), 2)

    def test_callback_names_are_grouped(self):
        self.assertEqual(analytics.norm_callback("svc:12"), "svc")
        self.assertEqual(analytics.norm_callback("ord:ok"), "ord:ok")
        self.assertEqual(analytics.norm_callback("day:2026-11-10"), "day")
        self.assertEqual(analytics.norm_callback(None), "—")


async def seed():
    await db.init_db()
    await db.execute("DELETE FROM analytics_events")
    ins = ("INSERT INTO analytics_events (ts, source, vid, sid, type, path, target, value, state, device, browser, os, ref) "
           "VALUES (datetime('now', ?), ?,?,?,?,?,?,?,?,?,?,?,?)")
    web = [
        # посетитель A: главная → форма, начал заполнять телефон, ушёл; сидел 40 секунд
        ("-60 seconds", "web", "A", "sa", "pv", "/", None, None, None, "mobile", "Safari", "iOS", "yandex.ru"),
        ("-45 seconds", "web", "A", "sa", "scroll", "/", None, 50, None, "mobile", "Safari", "iOS", None),
        ("-40 seconds", "web", "A", "sa", "click", "/", "Оставить заявку → /request", None, None, "mobile", "Safari", "iOS", None),
        ("-38 seconds", "web", "A", "sa", "pv", "/request", None, None, None, "mobile", "Safari", "iOS", None),
        ("-30 seconds", "web", "A", "sa", "focus", "/request", "phone", None, None, "mobile", "Safari", "iOS", None),
        ("-20 seconds", "web", "A", "sa", "leave", "/request", None, 18, None, "mobile", "Safari", "iOS", None),
        # посетитель B: зашёл и сразу ушёл
        ("-30 seconds", "web", "B", "sb", "pv", "/", None, None, None, "desktop", "Chrome", "Windows", None),
        ("-29 seconds", "web", "B", "sb", "leave", "/", None, 3, None, "desktop", "Chrome", "Windows", None),
    ]
    bot = [
        ("-300 seconds", "bot", "tg:1", "b1", "cmd", None, "/start", None, None, None, None, None, None),
        ("-290 seconds", "bot", "tg:1", "b1", "btn", None, "menu:order", None, "Menu:main", None, None, None, None),
        ("-280 seconds", "bot", "tg:1", "b1", "btn", None, "svc", None, "Order:service", None, None, None, None),
        ("-200 seconds", "bot", "tg:1", "b1", "msg", None, "text", None, "Order:address", None, None, None, None),
        ("-100 seconds", "bot", "tg:2", "b2", "cmd", None, "/start", None, None, None, None, None, None),
    ]
    for r in web + bot:
        await db.execute(ins, r)


class Metrics(unittest.TestCase):
    def collect(self, source):
        async def t():
            await seed()
            return await analytics.collect(source, "7")
        return LOOP.run_until_complete(t())

    def test_web_totals(self):
        m = self.collect("web")
        self.assertEqual(m["visitors"], 2)
        self.assertEqual(m["sessions"], 2)
        self.assertEqual(m["pageviews"], 3)
        self.assertEqual(m["new_visitors"], 2)

    def test_traffic_counted_without_any_order_or_client(self):
        # посетители видны в трафике, даже если никто ничего не заказал
        async def t():
            await seed()
            n_orders = (await db.fetch_one("SELECT COUNT(*) AS n FROM orders"))["n"]
            return n_orders, await analytics.collect("web", "7")
        n_orders, m = LOOP.run_until_complete(t())
        self.assertEqual(n_orders, 0)
        self.assertGreater(m["visitors"], 0)
        self.assertGreater(m["pageviews"], 0)

    def test_web_average_time_uses_activity_gaps(self):
        m = self.collect("web")
        # A: 60→20 секунд назад = 40 с (все промежутки до минуты); B: 1 с  → среднее ≈ 20 с
        self.assertEqual(m["avg_session"], 20)

    def test_web_quick_exits(self):
        self.assertEqual(self.collect("web")["quick"], 1)

    def test_web_top_pages_and_exit_pages(self):
        m = self.collect("web")
        self.assertEqual(m["pages"][0]["path"], "/")
        self.assertEqual(m["pages"][0]["views"], 2)
        self.assertEqual({e["path"]: e["n"] for e in m["exits"]}, {"/request": 1, "/": 1})

    def test_web_clicks_scroll_devices_sources(self):
        m = self.collect("web")
        self.assertEqual(m["clicks"][0]["target"], "Оставить заявку → /request")
        self.assertEqual(m["scroll"][50], 1)
        self.assertEqual({d["name"]: d["n"] for d in m["devices"]}, {"Телефон": 1, "Компьютер": 1})
        self.assertEqual({d["name"]: d["n"] for d in m["sources"]}, {"Яндекс (поиск)": 1, "Напрямую (адрес или закладка)": 1})

    def test_web_form_funnel_shows_where_people_stopped(self):
        m = self.collect("web")
        self.assertEqual(m["fields"], [{"target": "phone", "n": 1}])
        self.assertEqual(m["funnel"], [("Зашли на главную", 2), ("Открыли форму заявки", 1),
                                       ("Начали заполнять", 1), ("Отправили заявку", 0)])

    def test_bot_totals_and_buttons(self):
        m = self.collect("bot")
        self.assertEqual((m["visitors"], m["sessions"], m["events"]), (2, 2, 5))
        self.assertEqual(m["avg_session"], 50)  # b1: паузы 10 + 10 + 80 = 100 с, b2: 0 → (100 + 0) / 2 = 50
        self.assertEqual({b["target"]: b["n"] for b in m["buttons"]}, {"menu:order": 1, "svc": 1})
        self.assertEqual({b["target"]: b["n"] for b in m["commands"]}, {"/start": 2})

    def test_bot_exit_point_shows_last_action_and_state(self):
        m = self.collect("bot")
        stops = {(x["what"], x["state"]): x["n"] for x in m["stops"]}
        self.assertEqual(stops[("text", "Order:address")], 1)

    def test_bot_funnel(self):
        m = self.collect("bot")
        self.assertEqual([n for _, n in m["funnel"][:3]], [2, 1, 1])


class Retention(unittest.TestCase):
    def test_old_events_are_removed(self):
        async def t():
            await seed()
            await db.execute("UPDATE analytics_events SET ts=datetime('now','-800 days') WHERE vid='B'")
            gone = await analytics.cleanup()
            left = await db.fetch_one("SELECT COUNT(*) AS n FROM analytics_events")
            return gone, left["n"]
        gone, left = LOOP.run_until_complete(t())
        self.assertEqual(gone, 2)
        self.assertEqual(left, 11)


class Page(unittest.TestCase):
    def test_login_required(self):
        async def t(c):
            html = await (await c.get("/portal/analytics")).text()
            self.assertNotIn("Посетители", html)
        run(t)

    def test_page_shows_both_sources_and_has_its_own_button(self):
        async def t(c):
            await seed()
            html = await (await c.get("/portal/analytics?src=web&p=7")).text()
            for word in ("Посетители", "Сессии", "Средняя длительность", "Популярные страницы", "Куда нажимали",
                         "Где уходят", "До какого вопроса дошли", "С каких устройств", "Откуда пришли"):
                self.assertIn(word, html)
            self.assertIn("/portal/analytics?src=bot", html)
            bot = await (await c.get("/portal/analytics?src=bot&p=7")).text()
            for word in ("Кнопки", "Команды", "На чём остановились", "Путь клиента"):
                self.assertIn(word, bot)
            home = await (await c.get("/portal")).text()
            self.assertIn('href="/portal/analytics"', home)
        run(t, admin=True)

    def test_csv_export(self):
        async def t(c):
            await seed()
            r = await c.get("/portal/analytics.csv?p=all")
            self.assertEqual(r.status, 200)
            self.assertIn("csv", r.headers["Content-Type"])
            text = await r.text()
            self.assertIn("Оставить заявку", text)
            self.assertEqual(len(text.strip().splitlines()), 14)  # шапка + 13 событий
        run(t, admin=True)


if __name__ == "__main__":
    unittest.main()
