"""Цена зоотакси по маршруту — по формуле Яндекс Такси (просьба владельца: «стоимость такси от балды»).

Формула Яндекс Go («Эконом», Москва): минимальная стоимость включает первые 3 мин и 1 км, дальше —
каждый километр и каждая минута в пути, плюс опция «перевозка домашнего животного».
Расстояние и время — по дорогам (OSRM), время с поправкой на пробки; без маршрутизатора — по прямой."""
import asyncio
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ["DB_PATH"] = "/tmp/zoobot_taxi_price_test.db"
for s in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + s)
    except FileNotFoundError:
        pass

from aiogram.fsm.context import FSMContext  # noqa: E402
from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.fsm.storage.memory import MemoryStorage  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
import keyboards as kb  # noqa: E402
import pricing  # noqa: E402
import taxi  # noqa: E402
from config import config  # noqa: E402
from handlers import client as h  # noqa: E402
from handlers.client import Order  # noqa: E402
from utils import to_db  # noqa: E402

LOOP = asyncio.new_event_loop()
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=config.tz)
RYBALKO = (55.7936, 37.4960)      # ул. Маршала Рыбалко, 9 (подача со скриншота владельца)
VARSHAVKA = (55.6905, 37.6215)    # Варшавское шоссе, 36Б


def run(fn):
    async def go():
        await db.init_db()
        return await fn()
    return LOOP.run_until_complete(go())


def osrm(km, minutes):
    return {"code": "Ok", "routes": [{"distance": km * 1000, "duration": minutes * 60, "legs": []}],
            "waypoints": [{"name": "A"}, {"name": "B"}]}


class FareTest(unittest.TestCase):
    """Формула: посадка + км сверх включённых × ₽/км + минуты сверх включённых × ₽/мин + животное."""

    def test_yandex_comfort_numbers(self):
        t = taxi.Tariff()
        self.assertEqual((t.incl_min, t.incl_km, t.per_km, t.per_min, t.pet), (3, 1, 13, 13, 150))
        self.assertEqual(taxi.MINIMUM, 189)

    def test_long_trip(self):
        items = taxi.fare(taxi.Route(km=12.4, minutes=35, by_road=True), taxi.Tariff(), 189, "Зоотакси")
        amounts = [a for _, a in items]
        self.assertEqual(amounts, [189, 148, 416, 150])  # 11,4 км × 13; 32 мин × 13
        self.assertIn("посадка", items[0][0])
        self.assertIn("12,4 км", items[1][0])
        self.assertIn("35 мин", items[2][0])
        self.assertIn("животн", items[3][0])

    def test_short_trip_is_minimum_plus_pet(self):
        items = taxi.fare(taxi.Route(km=0.8, minutes=2, by_road=True), taxi.Tariff(), 189, "Зоотакси")
        self.assertEqual([a for _, a in items], [189, 150])

    def test_without_route_minimum_and_pet_only(self):
        items = taxi.fare(None, taxi.Tariff(), 189, "Зоотакси")
        self.assertEqual([a for _, a in items], [189, 150])

    def test_route_from_dict(self):
        r = taxi.Route.of({"km": 5.0, "minutes": 14, "by_road": False})
        self.assertEqual(r, taxi.Route(km=5.0, minutes=14, by_road=False))
        self.assertIsNone(taxi.Route.of(None))
        self.assertIsNone(taxi.Route.of({"km": "x"}))


class RouteTest(unittest.TestCase):
    def test_osrm_answer_with_traffic(self):
        r = taxi.parse_osrm(osrm(14.23, 20), traffic=1.5)
        self.assertEqual(r, taxi.Route(km=14.2, minutes=30, by_road=True))

    def test_bad_osrm_answers(self):
        for bad in (None, {}, {"code": "NoRoute", "routes": []}, {"code": "Ok", "routes": []},
                    {"code": "Ok", "routes": [{"distance": "x"}]}, [1, 2]):
            self.assertIsNone(taxi.parse_osrm(bad, traffic=1.5), bad)

    def test_straight_line_fallback(self):
        r = taxi.straight(RYBALKO, VARSHAVKA)
        self.assertFalse(r.by_road)
        self.assertTrue(16 < r.km < 21, r)               # ≈ 14 км по прямой × 1,4
        self.assertEqual(r.minutes, round(r.km / taxi.CITY_KMH * 60))

    def test_route_uses_router_then_falls_back(self):
        async def go():
            with mock.patch.object(taxi, "_osrm", mock.AsyncMock(return_value=osrm(17.0, 24))):
                road = await taxi.route(RYBALKO, VARSHAVKA)
            with mock.patch.object(taxi, "_osrm", mock.AsyncMock(return_value=None)):
                line = await taxi.route(RYBALKO, VARSHAVKA)
            return road, line
        road, line = run(go)
        self.assertEqual(road, taxi.Route(km=17.0, minutes=36, by_road=True))
        self.assertFalse(line.by_road)

    def test_osrm_request_url(self):
        self.assertEqual(taxi.osrm_path(RYBALKO, VARSHAVKA),
                         "/route/v1/driving/37.496,55.7936;37.6215,55.6905?overview=false&alternatives=false")


class TariffSettingsTest(unittest.TestCase):
    def test_admin_can_change_rates(self):
        async def go():
            await taxi.set_param("км", "17")
            await taxi.set_param("животное", "200")
            t = await taxi.get_tariff()
            await db.execute("DELETE FROM kv WHERE key=?", (taxi.KV_KEY,))
            return t
        t = run(go)
        self.assertEqual((t.per_km, t.pet, t.per_min), (17, 200, 13))

    def test_bad_values_refused(self):
        async def go():
            out = []
            for name, value in (("км", "-1"), ("км", "abc"), ("что-то", "5"), ("пробки", "9"), ("мин", "1000")):
                try:
                    await taxi.set_param(name, value)
                    out.append((name, value, "accepted"))
                except ValueError:
                    pass
            return out
        self.assertEqual(run(go), [])

    def test_taxi_base_price_is_yandex_minimum(self):
        async def go():
            return await db.fetch_one("SELECT base_price FROM services WHERE code='taxi'")
        self.assertEqual(run(go)["base_price"], 189)


class AdminCommandTest(unittest.TestCase):
    """/taxi_tariff без параметров показывает ставки, с параметрами — меняет (только админ: роутер admin фильтрует по id)."""

    def _call(self, args):
        from aiogram.filters import CommandObject
        from handlers import admin

        async def go():
            m = _msg(111)
            await admin.cmd_taxi_tariff(m, CommandObject(command="taxi_tariff", args=args))
            await db.execute("DELETE FROM kv WHERE key=?", (taxi.KV_KEY,))
            return m.answer.await_args.args[0]
        return run(go)

    def test_show_rates(self):
        text = self._call(None)
        self.assertIn("189", text)
        self.assertIn("13 ₽/км", text)
        self.assertIn("150", text)

    def test_change_rate(self):
        self.assertIn("17 ₽/км", self._call("км 17"))

    def test_bad_rate_explains(self):
        self.assertIn("допустимо", self._call("км -5"))
        self.assertIn("Нет такой ставки", self._call("чтото 5"))
        self.assertIn("Формат", self._call("км"))


class PricingTest(unittest.TestCase):
    def _svc(self):
        async def go():
            return await db.fetch_one("SELECT * FROM services WHERE code='taxi'")
        return run(go)

    def test_taxi_price_comes_from_route_not_flat(self):
        sid = self._svc()["id"]

        async def go():
            return await pricing.calculate_price(sid, {
                "quantity": 1, "start_at": NOW + timedelta(days=1), "route": {"km": 12.4, "minutes": 35, "by_road": True}})
        total, breakdown = run(go)
        self.assertEqual(total, 189 + 148 + 416 + 150)
        self.assertNotIn(1200, [a for _, a in breakdown])

    def test_rules_still_apply_on_top(self):
        sid = self._svc()["id"]

        async def go():
            return await pricing.calculate_price(sid, {
                "quantity": 1, "start_at": NOW + timedelta(days=1), "outside_mkad": True,
                "route": {"km": 30.0, "minutes": 50, "by_road": True}})
        total, breakdown = run(go)
        self.assertIn("Выезд за МКАД", [n for n, _ in breakdown])
        self.assertEqual(total, 189 + 377 + 611 + 150 + 150)


async def _state(uid, **data):
    await db.execute("INSERT OR IGNORE INTO users (tg_id, name, phone, address) VALUES (?,?,?,?)",
                     (uid, "Максим", "+79001234567", "Вадковский переулок, 3А с2"))
    user = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (uid,))
    await db.execute("DELETE FROM pets WHERE user_id=?", (user["id"],))
    await db.execute("INSERT INTO pets (user_id, name, species) VALUES (?,?,?)", (user["id"], "Людвиг", "енот"))
    pet = await db.fetch_one("SELECT id FROM pets WHERE user_id=?", (user["id"],))
    svc = await db.fetch_one("SELECT id FROM services WHERE code='taxi'")
    state = FSMContext(storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=uid, user_id=uid))
    await state.update_data(service_id=svc["id"], pet_id=pet["id"], quantity=1,
                            start_at=to_db(NOW + timedelta(days=1)), **data)
    return state


def _msg(uid):
    m = mock.MagicMock()
    m.answer = mock.AsyncMock()
    m.from_user = mock.MagicMock(id=uid, username=None, first_name="Максим")
    return m


class FlowKeepsCoordinatesTest(unittest.TestCase):
    """Точка подачи и назначения: координаты с карты/поиска доходят до расчёта маршрута."""

    def test_map_pick_keeps_coordinates_for_pickup(self):
        async def go():
            state = await _state(5551101)
            await state.set_state(Order.point_a)
            m = _msg(5551101)
            m.web_app_data = mock.MagicMock(data=json.dumps(
                {"lat": RYBALKO[0], "lon": RYBALKO[1], "label": "улица Маршала Рыбалко, 9, Щукино", "flat": ""}))
            with mock.patch.object(geo, "reverse", mock.AsyncMock(return_value=None)):
                await h.addr_webapp(m, state)
            cb = mock.MagicMock(data="ad:0")
            cb.answer = mock.AsyncMock()
            cb.from_user = m.from_user
            cb.message = _msg(5551101)
            await h.cb_addr(cb, state)
            return await state.get_data(), await state.get_state(), m.answer.await_args_list[0]
        data, st, check = run(go)
        self.assertEqual(st, Order.point_b.state)
        self.assertEqual(data["pt_a"], [RYBALKO[0], RYBALKO[1]])
        # подтверждение с карты — понятной кнопкой «Да», а не повтором адреса
        first = check.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertTrue(first.text.startswith("✅"), first.text)
        self.assertEqual(first.callback_data, "ad:0")

    def test_search_pick_keeps_coordinates_for_destination(self):
        async def go():
            state = await _state(5551102, address="улица Маршала Рыбалко, 9", pt_a=list(RYBALKO))
            await state.set_state(Order.point_b)
            m = _msg(5551102)
            place = geo.Place(label="Варшавское шоссе, 36Б с6, Нагорный район, Москва", district="Нагорный район",
                              lat=VARSHAVKA[0], lon=VARSHAVKA[1], outside_mkad=False, has_house=True)
            with mock.patch.object(geo, "search", mock.AsyncMock(return_value=[place])):
                await h._address_input(m, state, "Варшавское шоссе 36Б с6", "b", Order.point_b.state, m.from_user)
            cb = mock.MagicMock(data="ad:0")
            cb.answer = mock.AsyncMock()
            cb.from_user = m.from_user
            cb.message = _msg(5551102)
            with mock.patch.object(taxi, "route", mock.AsyncMock(return_value=taxi.Route(17.0, 36, True))) as r, \
                    mock.patch.object(h, "now_utc", return_value=NOW):
                await h.cb_addr(cb, state)
            return await state.get_data(), r.await_args, cb.message
        data, call, msg = run(go)
        self.assertEqual(data["pt_b"], [VARSHAVKA[0], VARSHAVKA[1]])
        self.assertEqual(call.args, (tuple(RYBALKO), tuple(VARSHAVKA)))
        shown = msg.answer.await_args_list[-1].args[0]
        self.assertIn("Маршрут: ≈ 17 км", shown)
        self.assertIn("36 мин", shown)
        self.assertNotIn("1 200 ₽", shown)
        self.assertIn("от 976 ₽", " ".join(shown.split()))   # 189 + 16 км × 13 + 33 мин × 13 + 150

    def test_saved_address_without_coordinates_is_geocoded(self):
        async def go():
            state = await _state(5551103, address="Вадковский переулок, 3А с2", pt_a=None,
                                 address_to="Варшавское шоссе, 36Б", pt_b=list(VARSHAVKA))
            place = geo.Place(label="Вадковский переулок, 3А с2", district="Тверской", lat=55.7801, lon=37.5953,
                              outside_mkad=False, has_house=True)
            with mock.patch.object(geo, "search", mock.AsyncMock(return_value=[place])), \
                    mock.patch.object(taxi, "route", mock.AsyncMock(return_value=taxi.Route(13.0, 30, True))) as r:
                data = await h._ensure_route(state)
                again = await h._ensure_route(state)  # второй раз маршрут не пересчитывается
            return data, again, r.await_count, r.await_args
        data, again, n, call = run(go)
        self.assertEqual(data["route"], {"km": 13.0, "minutes": 30, "by_road": True})
        self.assertEqual(again["route"], data["route"])
        self.assertEqual(n, 1)
        self.assertEqual(call.args, ((55.7801, 37.5953), tuple(VARSHAVKA)))


    def test_route_recalculated_when_destination_changes(self):
        async def go():
            state = await _state(5551104, address="улица Маршала Рыбалко, 9", pt_a=list(RYBALKO),
                                 address_to="Варшавское шоссе, 36Б", pt_b=list(VARSHAVKA))
            with mock.patch.object(taxi, "route", mock.AsyncMock(return_value=taxi.Route(17.0, 36, True))):
                first = await h._ensure_route(state)
            await state.update_data(address_to="Ленинградский проспект, 1", pt_b=[55.79, 37.55])
            with mock.patch.object(taxi, "route", mock.AsyncMock(return_value=taxi.Route(4.0, 12, True))) as r:
                second = await h._ensure_route(state)
            return first["route"], second["route"], r.await_count
        first, second, n = run(go)
        self.assertEqual(first["km"], 17.0)
        self.assertEqual(second["km"], 4.0)
        self.assertEqual(n, 1)


class MapKnowsWhichPointTest(unittest.TestCase):
    def test_keyboard_tells_the_map_which_point(self):
        old = config.webapp_url
        object.__setattr__(config, "webapp_url", "https://zoo.example")
        try:
            a = kb.address_ask("a").keyboard[0][0].web_app.url
            b = kb.address_ask("b", frm=RYBALKO).keyboard[0][0].web_app.url
            plain = kb.address_ask().keyboard[0][0].web_app.url
        finally:
            object.__setattr__(config, "webapp_url", old)
        self.assertEqual(a, "https://zoo.example/map?pt=a")
        self.assertEqual(b, "https://zoo.example/map?pt=b&from=55.7936,37.496")
        self.assertEqual(plain, "https://zoo.example/map")

    def test_map_page_names_the_point_and_offers_my_location(self):
        html = (Path(__file__).resolve().parent.parent / "webapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn("Откуда забрать", html)
        self.assertIn("Куда поедем", html)
        self.assertIn("Я здесь", html)
        self.assertIn("geolocation", html)


if __name__ == "__main__":
    unittest.main()
