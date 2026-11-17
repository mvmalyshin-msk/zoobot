"""«Экспресс» виден всегда: переключатель в окнах месяца/часа/минут бота и веб-чата, хранится в заказе, бургунди в админке."""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_express_toggle_test.db")

import datepicker  # noqa: E402
import db  # noqa: E402
import handlers.client as client_h  # noqa: E402
import orders  # noqa: E402
import portal  # noqa: E402
from config import config  # noqa: E402

LOOP = asyncio.new_event_loop()
DAY = datetime(2026, 11, 18, 14, 0, tzinfo=config.tz)


def clock(h, m=0):
    now = datetime(2026, 11, 18, h, m, tzinfo=config.tz)
    return mock.patch.object(client_h, "now_utc", return_value=now.astimezone(timezone.utc))


def last_row(markup):
    return markup.inline_keyboard[-1]


def xp_buttons(markup):
    return [b for row in markup.inline_keyboard for b in row if (b.callback_data or "").startswith("xp:")]


class ToggleRow(unittest.TestCase):
    def test_no_row_by_default(self):
        self.assertEqual(xp_buttons(datepicker.month_markup(2026, 11, DAY)), [])

    def test_month_row_off_and_on(self):
        off = xp_buttons(datepicker.month_markup(2026, 11, DAY, express=False))
        self.assertEqual([b.callback_data for b in off], ["xp:1:m:2026-11"])
        self.assertIn("Экспресс", off[0].text)
        self.assertNotIn("✅", off[0].text)
        on = xp_buttons(datepicker.month_markup(2026, 11, DAY, express=True))
        self.assertEqual([b.callback_data for b in on], ["xp:0:m:2026-11"])
        self.assertIn("✅", on[0].text)

    def test_hours_and_minutes_rows_remember_the_window(self):
        day = DAY.date()
        h = xp_buttons(datepicker.hours_markup(day, DAY, express=False))
        self.assertEqual(h[0].callback_data, "xp:1:d:2026-11-18")
        m = xp_buttons(datepicker.minutes_markup(day, 19, DAY, express=True))
        self.assertEqual(m[0].callback_data, "xp:0:h:2026-11-18:19")

    def test_row_is_last_so_navigation_stays_where_it_was(self):
        mk = datepicker.hours_markup(DAY.date(), DAY, express=False)
        self.assertTrue(last_row(mk)[0].callback_data.startswith("xp:"))
        self.assertEqual(mk.inline_keyboard[-2][0].text, "◀ Другая дата")

    def test_night_row_is_inactive_with_reason(self):
        b = xp_buttons(datepicker.month_markup(2026, 11, DAY, express="na"))
        self.assertEqual([x.callback_data for x in b], ["xp:na"])
        self.assertIn("09:00", b[0].text)
        self.assertIn("20:00", b[0].text)

    def test_callbacks_fit_telegram_limit(self):
        for mk in (datepicker.month_markup(2026, 11, DAY, express=False),
                   datepicker.hours_markup(DAY.date(), DAY, express=False),
                   datepicker.minutes_markup(DAY.date(), 19, DAY, express=False)):
            for b in xp_buttons(mk):
                self.assertLessEqual(len(b.callback_data.encode()), 64)

    def test_parse_xp(self):
        self.assertEqual(datepicker.parse_xp("xp:1:m:2026-11")[0], True)
        want, kind, value, hour = datepicker.parse_xp("xp:0:h:2026-11-18:19")
        self.assertEqual((want, kind, value.isoformat(), hour), (False, "h", "2026-11-18", 19))
        for bad in ("xp:2:m:2026-11", "xp:1:t:2026-11-18:19:00", "xp:1:h:2026-11-18:25", "xp:on", "cal:m:2026-11", "xp:1:m:zzz"):
            self.assertIsNone(datepicker.parse_xp(bad), bad)


class Flag(unittest.TestCase):
    def test_flag_by_time_and_category(self):
        walk, boarding = {"category": "walk"}, {"category": "boarding"}
        with clock(12):
            self.assertIs(client_h.xp_flag({}, walk), False)
            self.assertIs(client_h.xp_flag({"express": True}, walk), True)
            self.assertIsNone(client_h.xp_flag({"express": True}, boarding))
        for h in (3, 8, 20, 23):
            with clock(h):
                self.assertEqual(client_h.xp_flag({"express": True}, walk), "na", h)


class Handler(unittest.TestCase):
    def run_cb(self, data, hour, state_data=None):
        cb, state = mock.AsyncMock(), mock.AsyncMock()
        cb.data = data
        state.get_data.return_value = dict(state_data or {"service_id": 1})
        svc = {"category": "walk", "unit": "час"}
        with clock(hour), mock.patch.object(client_h, "_service_of", return_value=svc), \
                mock.patch.object(client_h, "_edit", new=mock.AsyncMock()) as edit:
            LOOP.run_until_complete(client_h.cb_xp(cb, state))
        return cb, state, edit

    def test_turn_on_redraws_the_same_window_with_short_wait(self):
        cb, state, edit = self.run_cb("xp:1:d:2026-11-18", 12)
        state.update_data.assert_awaited_with(express=True)
        text, markup = edit.await_args.args[1], edit.await_args.args[2]
        self.assertIn("час", text)
        hours = [b.text for row in markup.inline_keyboard for b in row if b.callback_data.startswith("cal:h:")]
        self.assertIn("12:00", hours)  # при экспрессе (15 минут) 12:15 уже можно: час 12 виден
        self.assertEqual([b.callback_data for b in xp_buttons(markup)], ["xp:0:d:2026-11-18"])

    def test_turn_off_removes_short_wait(self):
        cb, state, edit = self.run_cb("xp:0:d:2026-11-18", 12, {"service_id": 1, "express": True})
        state.update_data.assert_awaited_with(express=False)
        markup = edit.await_args.args[2]
        hours = [b.text for row in markup.inline_keyboard for b in row if b.callback_data.startswith("cal:h:")]
        self.assertNotIn("12:00", hours)  # без экспресса запас 2 часа: 12:00 уже нельзя
        self.assertIn("14:00", hours)

    def test_night_is_refused(self):
        cb, state, edit = self.run_cb("xp:1:m:2026-11", 23)
        state.update_data.assert_not_awaited()
        edit.assert_not_awaited()
        self.assertTrue(cb.answer.await_args.kwargs.get("show_alert"))

    def test_na_button_explains(self):
        cb, state, edit = self.run_cb("xp:na", 23)
        self.assertIn("09:00", cb.answer.await_args.args[0])
        edit.assert_not_awaited()

    def test_broken_callback_is_harmless(self):
        cb, state, edit = self.run_cb("xp:1:zzz", 12)
        state.update_data.assert_not_awaited()
        edit.assert_not_awaited()


class Stored(unittest.TestCase):
    n = 9100

    @classmethod
    def setUpClass(cls):
        if os.path.exists(os.environ["DB_PATH"]):
            os.remove(os.environ["DB_PATH"])
        LOOP.run_until_complete(db.init_db())

    def make(self, express):
        async def go():
            Stored.n += 1
            uid = await db.execute("INSERT INTO users (tg_id, name, phone) VALUES (?,?,?)", (Stored.n, "Анна", "+79990001122"))
            svc = await db.fetch_one("SELECT id FROM services ORDER BY id LIMIT 1")
            now = datetime.now(config.tz) + timedelta(days=2)
            data = {"user_id": uid, "pet_id": None, "service_id": svc["id"], "start_at": now, "end_at": None,
                    "quantity": 1, "address": "Мира 1", "is_urgent": False, "outside_mkad": False}
            if express is not None:
                data["express"] = express
            oid = await orders.create_order(data, 1000, [], "test")
            return await orders.get_order_full(oid)
        return LOOP.run_until_complete(go())

    def test_express_saved_and_default_zero(self):
        self.assertEqual(self.make(True)["express"], 1)
        self.assertEqual(self.make(None)["express"], 0)

    def test_card_mentions_express_for_staff_and_client(self):
        o = self.make(True)
        self.assertIn("⚡ Экспресс", orders.order_card(o, for_staff=True))
        self.assertIn("⚡ Экспресс", orders.order_card(o))
        self.assertNotIn("Экспресс", orders.order_card(self.make(None), for_staff=True))


class PortalView(unittest.TestCase):
    def row(self, express, status="created"):
        return {"kind": "order", "id": 5, "status": status, "service": "Выгул", "category": "walk", "source": "tg",
                "name": "Анна", "phone": "+79001112233", "start_at": "2026-11-20 10:00:00", "price_total": 900,
                "created_at": "2026-11-17 09:00:00", "cancel_actor": None, "express": express}

    def test_live_express_order_is_burgundy_with_badge(self):
        html = portal._row_html(self.row(1))
        self.assertIn("ord express", html)
        self.assertIn("xbadge", html)
        self.assertIn("ЭКСПРЕСС", html)

    def test_plain_and_finished_are_not(self):
        self.assertNotIn("xbadge", portal._row_html(self.row(0)))
        done = portal._row_html(self.row(1, "completed"))
        self.assertNotIn("ord express", done)
        self.assertIn("xbadge", done)  # отметка остаётся, цвет — только пока заказ живой


class StaleExpress(unittest.TestCase):
    def test_express_is_ignored_when_admins_left(self):
        async def go():
            msg = mock.AsyncMock()
            state = mock.AsyncMock()
            state.get_data.return_value = {"service_id": 1, "express": True}
            svc = {"category": "walk", "unit": "час", "id": 1}
            dt = datetime(2026, 11, 18, 21, 0, tzinfo=config.tz)
            with clock(20, 30), mock.patch.object(db, "fetch_one", return_value=svc):
                return await client_h._accept_when(msg, state, mock.Mock(), dt), msg
        ok, msg = LOOP.run_until_complete(go())
        self.assertFalse(ok)  # 20:30, админы ушли: правило «2 часа до утра», а не 15 минут


class StaleAtConfirm(unittest.TestCase):
    def test_express_order_is_not_created_after_admins_left(self):
        async def go():
            cb, state = mock.AsyncMock(), mock.AsyncMock()
            cb.from_user = mock.Mock(id=5)
            start = datetime(2026, 11, 18, 20, 40, tzinfo=config.tz)
            state.get_data.return_value = {"service_id": 1, "express": True, "start_at": client_h.to_db(start)}
            svc = {"category": "walk", "unit": "час", "id": 1}
            with clock(20, 5), mock.patch.object(client_h.repo, "get_or_create_user", return_value={"id": 1}), \
                    mock.patch.object(client_h, "_service_of", return_value=svc), \
                    mock.patch.object(client_h, "ask_when", new=mock.AsyncMock()) as ask, \
                    mock.patch.object(client_h.orders, "create_order", new=mock.AsyncMock()) as create:
                await client_h.cb_order_ok(cb, state)
            return ask, create, state
        ask, create, state = LOOP.run_until_complete(go())
        create.assert_not_awaited()
        ask.assert_awaited()
        state.update_data.assert_any_await(express=False)


if __name__ == "__main__":
    unittest.main()
