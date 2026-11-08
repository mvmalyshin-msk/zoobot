"""Правки по замечаниям владельца: история после удаления, узнавание по телефону, список заказов, реквизиты оплаты."""
import asyncio
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "fb2.db"))

import db  # noqa: E402
import orders  # noqa: E402
import repo  # noqa: E402
import texts  # noqa: E402


class U:
    def __init__(self, uid, username=None):
        self.id, self.username = uid, username


async def reset():
    await db.init_db()
    for t in ("reviews", "reminders", "order_statuses_log", "payments", "orders", "pets", "consents", "users"):
        await db.execute(f"DELETE FROM {t}")


async def make_order(uid, status="completed", created="2026-11-01 10:00:00"):
    svc = await db.fetch_one("SELECT id FROM services LIMIT 1")
    await db.execute("INSERT INTO orders (user_id, service_id, status, start_at, address, price_total, created_at) "
                     "VALUES (?,?,?,?,?,?,?)", (uid, svc["id"], status, "2026-11-09 10:00:00", "Ленина 1", 500, created))


class History(unittest.TestCase):
    def test_orders_before_erasure_are_hidden_from_client(self):
        async def go():
            await reset()
            u = await repo.get_or_create_user(U(1))
            await make_order(u["id"])
            self.assertEqual(len(await repo.user_orders(u["id"])), 1)
            await repo.erase_user(u["id"])
            self.assertEqual(await repo.user_orders(u["id"]), [])
            self.assertIsNone(await repo.last_order(u["id"]))
            await make_order(u["id"], created="2999-01-01 00:00:00")  # после удаления
            self.assertEqual(len(await repo.user_orders(u["id"])), 1)
            self.assertEqual((await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"], 2)  # учёт цел
        asyncio.run(go())


class Recognise(unittest.TestCase):
    def test_find_and_copy_profile(self):
        async def go():
            await reset()
            old = await repo.get_or_create_user(U(1, "old"))
            await repo.update_user(old["id"], name="Максим", phone="+79800000000", address="Ленина 1", district="Химки")
            await repo.add_pet(old["id"], "Линда", "другое", "енот", "S", "лысеет")
            new = await repo.get_or_create_user(U(2, "new"))
            found = await repo.find_profile_by_phone("+79800000000", new["id"])
            self.assertEqual(found["id"], old["id"])
            self.assertIsNone(await repo.find_profile_by_phone("+79800000000", old["id"]))  # себя не находим
            self.assertIsNone(await repo.find_profile_by_phone("+79811111111", new["id"]))
            await repo.copy_profile(old["id"], new["id"])
            n = await repo.get_user(new["id"])
            self.assertEqual((n["name"], n["address"], n["district"], n["phone"]),
                             ("Максим", "Ленина 1", "Химки", "+79800000000"))
            pets = await repo.list_pets(new["id"])
            self.assertEqual([(p["name"], p["breed"], p["notes"]) for p in pets], [("Линда", "енот", "лысеет")])
            self.assertEqual(len(await repo.list_pets(old["id"])), 1)  # у старого ничего не пропало
        asyncio.run(go())

    def test_erased_profiles_are_not_found(self):
        async def go():
            await reset()
            old = await repo.get_or_create_user(U(1))
            await repo.update_user(old["id"], name="А", phone="+79800000000", address="Ленина 1")
            await repo.erase_user(old["id"])
            new = await repo.get_or_create_user(U(2))
            self.assertIsNone(await repo.find_profile_by_phone("+79800000000", new["id"]))
        asyncio.run(go())


def row(i, status, name="Выгул 30 минут"):
    return {"id": i, "status": status, "start_at": "2026-11-09 07:00:00", "price_total": 500, "name": name}


class MyOrders(unittest.TestCase):
    def test_active_first_and_highlighted_done_below(self):
        t = orders.format_my_orders([row(4, "cancelled"), row(3, "confirmed"), row(2, "completed"), row(1, "created")])
        self.assertLess(t.index("В работе"), t.index("№3"))
        self.assertLess(t.index("№1"), t.index("Завершённые и отменённые"))
        self.assertLess(t.index("Завершённые и отменённые"), t.index("№4"))
        self.assertIn("🟢", t.split("Завершённые")[0])
        self.assertNotIn("🟢", t.split("Завершённые")[1])

    def test_only_done(self):
        t = orders.format_my_orders([row(2, "cancelled")])
        self.assertNotIn("В работе", t)
        self.assertIn("№2", t)

    def test_only_active(self):
        t = orders.format_my_orders([row(2, "assigned")])
        self.assertNotIn("Завершённые и отменённые", t)


class LastOrder(unittest.TestCase):
    def test_menu_text_mentions_last_order(self):
        t = texts.menu_text({"name": "Выгул 30 минут", "start_at": "2026-11-09 07:00:00", "status": "created",
                             "pet_name": "Линда"})
        self.assertIn("Последний заказ", t)
        self.assertIn("Выгул 30 минут", t)
        self.assertIn("Линда", t)
        self.assertIn("09.11", t)

    def test_menu_text_without_orders(self):
        self.assertEqual(texts.menu_text(None), texts.WELCOME)


class Requisites(unittest.TestCase):
    def test_from_env(self):
        import payinfo
        env = {"PAY_SBP_PHONE": "+7 900 111-22-33", "PAY_SBP_BANK": "Т-Банк", "PAY_CARD": "2200 0000 0000 0000",
               "PAY_RECIPIENT": "Иван И."}
        self.assertIn("+7 900 111-22-33", payinfo.sbp_text(env, 12, 550))
        self.assertIn("Т-Банк", payinfo.sbp_text(env, 12, 550))
        self.assertIn("№12", payinfo.sbp_text(env, 12, 550))
        self.assertIn("2200 0000 0000 0000", payinfo.card_text(env, 12, 550))
        self.assertIn("550", payinfo.card_text(env, 12, 550))

    def test_missing_means_unavailable(self):
        import payinfo
        self.assertFalse(payinfo.available({}, "sbp"))
        self.assertFalse(payinfo.available({}, "card"))
        self.assertTrue(payinfo.available({"PAY_CARD": "1"}, "card"))
        self.assertFalse(payinfo.available({"PAY_CARD": "  "}, "card"))


class PaidPhrase(unittest.TestCase):
    def test_phrases(self):
        for t in ("оплатил", "Оплатила", "я оплатил", "оплатил!", "оплата прошла", "перевёл", "перевела деньги"):
            self.assertTrue(texts.is_paid_claim(t), t)
        for t in ("оплатить", "как оплатить?", "не оплатил", "оплатите", "привет", ""):
            self.assertFalse(texts.is_paid_claim(t), t)


# ---------------- Второй пакет: меню услуг, количество, узнавание, цена, карточка, админ-кнопки ----------------
import keyboards as kb  # noqa: E402
import pricing  # noqa: E402


def S(i, code, cat, name, price, unit="выгул"):
    return {"id": i, "code": code, "category": cat, "name": name, "base_price": price, "unit": unit, "active": 1}


ITEMS = [S(1, "walk30", "walk", "Выгул 30 минут", 400), S(2, "walk60", "walk", "Выгул 60 минут", 700),
         S(3, "sit_in", "sitter", "Зооняня приходящая", 600, "час"),
         S(4, "sit_live", "sitter", "Зооняня с проживанием", 2500, "сутки"),
         S(5, "b_home", "boarding", "Передержка в частном доме", 1500, "сутки"),
         S(6, "b_hotel", "boarding", "Передержка в зоогостинице", 1800, "сутки"),
         S(7, "taxi", "taxi", "Зоотакси", 1200, "поездка")]


def btns(markup):
    return [b for r in markup.inline_keyboard for b in r]


class Categories(unittest.TestCase):
    def test_variant_labels(self):
        for cat, name, want in (("walk", "Выгул 30 минут", "30 минут"), ("sitter", "Зооняня приходящая", "Приходящая"),
                                ("sitter", "Зооняня с проживанием", "С проживанием"),
                                ("boarding", "Передержка в частном доме", "В частном доме"),
                                ("taxi", "Зоотакси", "Зоотакси")):
            self.assertEqual(texts.variant_label(cat, name), want)

    def test_first_screen_has_only_four_services(self):
        m = kb.service_categories(ITEMS)
        top = [b for b in btns(m) if b.callback_data.startswith(("cat:", "svc:"))]
        self.assertEqual([b.callback_data for b in top], ["cat:walk", "cat:sitter", "cat:boarding", "svc:7"])
        self.assertIn("от 400", top[0].text)
        self.assertIn("от 600", top[1].text)
        self.assertIn("от 1 500", top[2].text)

    def test_inactive_category_hidden(self):
        items = [i for i in ITEMS if i["category"] != "boarding"]
        self.assertNotIn("cat:boarding", [b.callback_data for b in btns(kb.service_categories(items))])

    def test_second_screen_variants(self):
        m = kb.category_variants([i for i in ITEMS if i["category"] == "sitter"], "sitter")
        d = [b.callback_data for b in btns(m)]
        self.assertEqual(d[:2], ["svc:3", "svc:4"])
        self.assertIn("menu:order", d)
        self.assertTrue(any("Приходящая" in b.text and "600" in b.text for b in btns(m)))


class Quantity(unittest.TestCase):
    def test_other_button_and_rows(self):
        m = kb.quantity([1, 2, 3, 4, 6, 8, 10, 12], other=True)
        self.assertIn("qty:other", [b.callback_data for b in btns(m)])
        self.assertTrue(all(len(r) <= 4 for r in m.inline_keyboard))
        self.assertIn("qty:12", [b.callback_data for b in btns(m)])


class SecondFactor(unittest.TestCase):
    def test_pet_name_or_address(self):
        async def go():
            await reset()
            old = await repo.get_or_create_user(U(1))
            await repo.update_user(old["id"], name="Максим", phone="+79800000000",
                                   address="Железнодорожная улица, 33 к1, Сходня, Химки, кв. 33")
            await repo.add_pet(old["id"], "Линда", "другое", "енот", "S", None)
            self.assertTrue(await repo.match_second_factor(old["id"], "  линда "))
            self.assertTrue(await repo.match_second_factor(old["id"], "железнодорожная 33"))
            self.assertTrue(await repo.match_second_factor(old["id"], "Железнодорожная ул, 33 к1"))
            self.assertFalse(await repo.match_second_factor(old["id"], "Рекс"))
            self.assertFalse(await repo.match_second_factor(old["id"], "Химки"))       # один город — мало
            self.assertFalse(await repo.match_second_factor(old["id"], "кв 33"))       # только квартира — мало
            self.assertFalse(await repo.match_second_factor(old["id"], "Ленина 1"))
            self.assertFalse(await repo.match_second_factor(old["id"], ""))
        asyncio.run(go())


class Conditions(unittest.TestCase):
    SVC = {"unit": "выгул", "category": "walk"}

    def test_new_conditions_exist(self):
        for name in ("pet_danger", "pet_notes", "weekend", "pet_other"):
            self.assertIn(name, pricing.VALID_CONDITIONS)

    def test_checks(self):
        async def go():
            c = pricing.CONDITIONS
            self.assertTrue(await c["pet_danger"](self.SVC, {"pet_traits": "pull,aggr_dog"}))
            self.assertFalse(await c["pet_danger"](self.SVC, {"pet_traits": "pull"}))
            self.assertFalse(await c["pet_danger"](self.SVC, {}))
            self.assertTrue(await c["pet_notes"](self.SVC, {"pet_notes": "аллергия"}))
            self.assertFalse(await c["pet_notes"](self.SVC, {"pet_notes": ""}))
            sat = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)   # суббота
            mon = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
            self.assertTrue(await c["weekend"](self.SVC, {"start_at": sat}))
            self.assertFalse(await c["weekend"](self.SVC, {"start_at": mon}))
            self.assertTrue(await c["pet_other"](self.SVC, {"pet_species": "другое"}))
            self.assertFalse(await c["pet_other"](self.SVC, {"pet_species": "собака"}))
        asyncio.run(go())


class Card(unittest.TestCase):
    def test_comment_on_staff_card_without_contact_line(self):
        async def go():
            await reset()
            u = await repo.get_or_create_user(U(7, "max"))
            await repo.update_user(u["id"], name="Максим", phone="+79800000000", address="Ленина 1", contact_pref="call")
            pet = await repo.add_pet(u["id"], "Линда", "другое", "енот", "S", None)
            svc = await db.fetch_one("SELECT id FROM services LIMIT 1")
            from datetime import timedelta
            oid = await orders.create_order(
                {"user_id": u["id"], "pet_id": pet, "service_id": svc["id"],
                 "start_at": datetime.now(timezone.utc) + timedelta(days=3), "end_at": None, "quantity": 1,
                 "address": "Ленина 1", "address_to": None, "is_urgent": False, "outside_mkad": False,
                 "comment": "Домофон не работает, звоните"}, 500, [("Выгул", 500)], "client:7")
            o = await orders.get_order_full(oid)
            staff = orders.order_card(o, for_staff=True)
            self.assertIn("Комментарий: Домофон не работает, звоните", staff)
            self.assertNotIn("Связь:", staff)  # способ связи администратор выбирает сам
            client = orders.order_card(o)
            self.assertIn("Комментарий", client)
            self.assertNotIn("Связь:", client)
        asyncio.run(go())


class AdminHelpers(unittest.TestCase):
    def test_chat_url(self):
        self.assertEqual(orders.chat_url("max_user", 5), "https://t.me/max_user")
        self.assertEqual(orders.chat_url(None, 5), "tg://user?id=5")
        self.assertEqual(orders.chat_url("", 5), "tg://user?id=5")

    def test_new_order_keyboard(self):
        m = kb.admin_new_order(12, "https://t.me/max_user")
        d = [b.callback_data for b in btns(m) if b.callback_data]
        self.assertEqual(d, ["ap:ok:12", "ap:no:12"])
        self.assertEqual([b.url for b in btns(m) if b.url], ["https://t.me/max_user"])

    def test_pending_payment_orders(self):
        async def go():
            await reset()
            u = await repo.get_or_create_user(U(1, "a"))
            await repo.update_user(u["id"], name="Анна", phone="+79000000000")
            await make_order(u["id"], "confirmed")
            await make_order(u["id"], "created")
            await make_order(u["id"], "paid")
            for oid in (r["id"] for r in await db.fetch_all("SELECT id FROM orders")):
                await db.execute("INSERT INTO payments (order_id, amount) VALUES (?,?)", (oid, 500))
            rows = await orders.pending_payment_orders()
            self.assertEqual([r["status"] for r in rows], ["confirmed"])
            self.assertEqual(rows[0]["client_name"], "Анна")
        asyncio.run(go())

    def test_pay_options_keyboard(self):
        m = kb.pay_options(5, "https://pay.example/x", sbp=True, card=False)
        self.assertEqual([b.url for b in btns(m) if b.url], ["https://pay.example/x"])
        d = [b.callback_data for b in btns(m) if b.callback_data]
        self.assertEqual(d, ["pay:sbp:5", "pay:claim:5"])
        m2 = kb.pay_options(5, None, sbp=True, card=True)
        self.assertEqual([b.callback_data for b in btns(m2)], ["pay:sbp:5", "pay:card:5", "pay:claim:5"])

    def test_admin_menu_has_no_order_button(self):
        d = [b.callback_data for b in btns(kb.admin_menu())]
        self.assertTrue(d)
        self.assertNotIn("menu:order", d)
        self.assertTrue(all(x.startswith("adm:") for x in d))


if __name__ == "__main__":
    unittest.main()
