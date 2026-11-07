"""Фаззинг: случайные сообщения, стикеры, геопозиции, кнопки и «битые» кнопки во всех состояниях.

Ищем: необработанные исключения в хендлерах, ответы, которые Telegram не примет
(пустой текст, > 4096 знаков, callback_data > 64 байт), «зависшие» диалоги.
Запуск:  BOT_TOKEN=1:x ADMIN_IDS=111 DB_PATH=/tmp/zoobot_fuzz.db python tests/fuzz_test.py [seed] [steps]
"""
import asyncio
import os
import random
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_fuzz.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + suffix)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.methods import SendMessage  # noqa: E402
from aiogram.types import Chat, InlineKeyboardMarkup, Message  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
from bot import build_dispatcher  # noqa: E402
from scheduler import process_due_reminders  # noqa: E402

ADMIN = 111
CLIENTS = [1001, 1002, 1003]
EXEC = 2000
SENT: list[dict] = []
PROBLEMS: list[str] = []


class FakeSession(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            t = method.text or ""
            if not t.strip():
                PROBLEMS.append(f"пустой текст сообщения для {method.chat_id}")
            if len(t) > 4096:
                PROBLEMS.append(f"текст {len(t)} знаков (>4096): {t[:60]!r}")
            mk = method.reply_markup
            if isinstance(mk, InlineKeyboardMarkup):
                for row in mk.inline_keyboard:
                    for b in row:
                        if b.callback_data is not None and not 1 <= len(b.callback_data.encode()) <= 64:
                            PROBLEMS.append(f"callback_data {len(b.callback_data.encode())} байт: {b.callback_data!r}")
                        if not b.text.strip():
                            PROBLEMS.append("кнопка без текста")
            SENT.append({"to": method.chat_id, "text": t, "markup": mk})
            return Message(message_id=len(SENT), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=t or "x")
        return True


bot = Bot("1:x", session=FakeSession())
dp = build_dispatcher()
dp.errors.handlers.clear()  # в фаззинге ошибки должны попадать в список проблем, а не гаситься


@dp.errors()
async def on_error(event):
    tb = "".join(traceback.format_exception(event.exception)[-3:])
    PROBLEMS.append(f"ИСКЛЮЧЕНИЕ в хендлере: {event.exception!r}\n{tb}")
    return True


# --- карта подменена ---
async def fake_search(text):
    k = sum(map(ord, text)) % 5
    if k == 0:
        return None
    if k == 1:
        return []
    return [geo.Place(f"ул. Тестовая, {i}, Химки", "Химки", 55.9, 37.4, bool(i % 2), bool(i % 3)) for i in range(1, 1 + k)]


async def fake_reverse(lat, lon):
    if abs(lat) > 80:
        return None
    return geo.Place("ул. Гео, 1, Москва", "Москва", lat, lon, False, lat > 0)


geo.search, geo.reverse = fake_search, fake_reverse

_uid = 0


def _user(uid):
    return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"u{uid}"}


async def feed(update: dict) -> None:
    global _uid
    _uid += 1
    update["update_id"] = _uid
    await dp.feed_raw_update(bot, update)


def _msg(uid, **kw):
    global _uid
    _uid += 1
    return {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
            "from": _user(uid), **kw}


async def say(uid, text):
    m = _msg(uid, text=text)
    if text.startswith("/"):
        m["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    await feed({"message": m})


async def callback(uid, data):
    global _uid
    _uid += 1
    await feed({"callback_query": {"id": str(_uid), "from": _user(uid), "chat_instance": "ci", "data": data,
                                   "message": {"message_id": _uid, "date": int(time.time()),
                                               "chat": {"id": uid, "type": "private"},
                                               "from": {"id": 999, "is_bot": True, "first_name": "bot"},
                                               "text": "x"}}})


def buttons(uid):
    for m in reversed([s for s in SENT if s["to"] == uid]):
        if isinstance(m["markup"], InlineKeyboardMarkup):
            return [b.callback_data for row in m["markup"].inline_keyboard for b in row if b.callback_data]
    return []


TEXTS = [
    "/start", "/menu", "/profile", "/orders", "/stats", "/admin", "/my", "/cancel", "/start extra", "/MENU",
    "", " ", "   \n  ", "а", "Анна", "Иван Петров", "+7 900 123-45-67", "89001234567", "123", "0", "-1", "99999999999999999999",
    "завтра 18:30", "сегодня 23:59", "12.11 09:00", "31.02 10:00", "25:99", "01.01.2020 10:00", "01.01.2099 10:00",
    "Химки", "ул. Ленина, 5, кв. 12", "Москва, Тверская 7", "ывфвф 5", "Рекс", "Лабрадор", "метис", "-", "хомяк",
    "большой", "10 кг", "S", "M", "L", "😀🐕🐈", "<b>bold</b>", "<script>alert(1)</script>", "'; DROP TABLE users;--",
    "%s %d {0} {id}", "{service}", "\x00null", "a" * 5000, "я" * 300, "‮rtl‬", "​", "ё" * 60, "Анна\nИван", "\t",
    "1", "2", "3", "5", "100", "да", "нет", "отмена",
]
CMDS = [
    "/confirm", "/confirm abc", "/confirm 1", "/confirm 99999", "/confirm -1", "/paid 1", "/paid x", "/progress 1", "/done 1",
    "/cancel 1 причина", "/cancel x", "/assign 1 1", "/assign 1", "/assign a b", "/assign 99 99", "/send_payment 1 https://x.y/p",
    "/send_payment 1", "/send_payment x y", "/send_payment 1 javascript:alert(1)", "/price_set 1 500", "/price_set 1 -5",
    "/price_set x y", "/price_set 999 10", "/price_set 1 99999999999999", "/rule_add 0 holiday percent 20 Тест", "/rule_add",
    "/rule_add 0 nonsense fixed 10 x", "/rule_add 1 a b c d", "/rule_del 1", "/rule_del x", "/rule_del 99999",
    "/add_executor Мария | +79005554433 | self_employed | walk | Химки | 70% | 2000",
    "/add_executor Петя | +79001112233 | individual | walk | Химки | 300 | 2001 | 01.01.2012 | да | реквизиты",
    "/add_executor", "/add_executor a|b", "/add_executor " + "x|" * 30, "/executors", "/orders", "/stats", "/stats 99.99",
    "/broadcast", "/broadcast привет <b>всем</b>", "/broadcast " + "ф" * 4000, "/admin", "/exec", "/refund 1", "/reviews",
]
CB_GARBAGE = [
    "", "x", "menu:order", "menu:orders", "menu:repeat", "menu:profile", "menu:home", "svc:1", "svc:999", "svc:abc", "svc:",
    "pick:1", "pick:x", "pick:99999", "qty:1", "qty:x", "qty:-5", "qty:99999", "ord:ok", "ord:cancel", "ord:mkad", "ord:addr", "ord:pet",
    "pa:profile", "sp:собака", "sp:", "sp:дракон", "sz:L", "sz:XXL", "sz:", "pn:none", "pb:0", "pb:99", "pb:x", "pb:keep", "pb:retry",
    "ad:0", "ad:5", "ad:x", "ad:keep", "ad:retry", "rate:1:5", "rate:x:y", "rate:99:9", "rating:1:5", "rating:1:0", "rating:1:6",
    "review:skip", "cancel:1", "cancel:x", "cancel_yes:1", "cancel_yes:999", "ex:start:1", "ex:done:1", "ex:x", "ex:start:x", "ex:a:b:c",
    "profile:edit", "profile:addpet", "profile:marketing", "mkt:on", "mkt:off", "pdn:agree", "pdn:no", "a" * 70, "😀",
]


SMART = [  # по подсказке бота: что нормальный человек ответил бы
    ("обращаться", ["Анна", "Иван Петров"]), ("телефон", ["+7 900 123-45-67", "89161234567"]),
    ("Адрес", ["Химки, Ленина 5, кв 3", "Москва, Тверская 7"]), ("адрес", ["Химки, Ленина 5", "Москва, Арбат 1"]),
    ("зовут", ["Рекс", "Мурка", "Белка"]), ("Порода", ["лабрадор", "метис", "пудель", "лабродор"]),
    ("Кто это", ["хомяк", "попугай"]), ("Когда", ["завтра 18:30", "сегодня 23:30", "12.11 09:00", "завтра 22:30", "послезавтра 06:30"]),
    ("Размер", ["большой", "5 кг", "20 кг"]), ("Особенности", ["боится грозы", "аллергия на курицу"]),
    ("Сколько", ["2", "3", "5"]), ("часов", ["2", "4"]), ("суток", ["1", "3"]), ("Отзыв", ["Отлично!", "ок"]),
]


async def smart_step(rnd, uid):
    last = next((m for m in reversed(SENT) if m["to"] == uid), None)
    b = buttons(uid)
    if last and rnd.random() < 0.6:
        for key, answers in SMART:
            if key in last["text"] and not (isinstance(last["markup"], InlineKeyboardMarkup) and "Выберите" in last["text"]):
                await say(uid, rnd.choice(answers))
                return
    if b:
        prefer = [x for x in b if x.startswith(("ord:ok", "svc:", "sp:", "sz:", "pn:", "qty:", "pa:", "ad:", "pb:", "pdn"))]
        await callback(uid, rnd.choice(prefer if prefer and rnd.random() < 0.8 else b))
    else:
        await say(uid, "/menu")


def rand_update_kind(rnd):
    garbage = 0 if os.environ.get("FUZZ_REAL_ONLY") else 8  # 0: нажимаем только настоящие кнопки
    return rnd.choices(["smart", "text", "cmd", "btn", "garbage", "sticker", "photo", "contact", "location", "voice", "admin", "webapp"],
                       [60, 12, 3, 12, garbage, 2, 2, 2, 2, 1, 8, 4])[0]


async def step(rnd, uid):
    kind = rand_update_kind(rnd)
    if kind == "smart":
        await smart_step(rnd, uid)
    elif kind == "text":
        await say(uid, rnd.choice(TEXTS))
    elif kind == "cmd":
        await say(uid, rnd.choice(["/start", "/menu", "/profile", "/orders", "/admin", "/stats"]))
    elif kind == "btn":
        b = buttons(uid)
        if b:
            await callback(uid, rnd.choice(b))
        else:
            await say(uid, "/menu")
    elif kind == "garbage":
        await callback(uid, rnd.choice(CB_GARBAGE))
    elif kind == "sticker":
        await feed({"message": _msg(uid, sticker={"file_id": "f", "file_unique_id": "u", "type": "regular",
                                                  "width": 5, "height": 5, "is_animated": False, "is_video": False})})
    elif kind == "photo":
        await feed({"message": _msg(uid, photo=[{"file_id": "f", "file_unique_id": "u", "width": 5, "height": 5}])})
    elif kind == "voice":
        await feed({"message": _msg(uid, voice={"file_id": "f", "file_unique_id": "u", "duration": 1})})
    elif kind == "contact":
        ph = rnd.choice(["79001234567", "abc", "", "+7", "1" * 30, "8 (900) 123-45-67"])
        await feed({"message": _msg(uid, contact={"phone_number": ph, "first_name": "x", "user_id": uid})})
    elif kind == "webapp":
        payload = rnd.choice([
            '{"lat":55.9,"lon":37.3,"label":"Химки, Ленина 5","flat":"12"}', '{"lat":55.97,"lon":37.2,"label":"Зеленоград 1105"}',
            '{"lat":0,"lon":0,"label":"x"}', "not json", "[]", "", "{" * 500, '{"lat":"a","lon":null,"label":5}',
            '{"lat":55.9,"lon":37.3,"label":"' + "я" * 3000 + '"}', '{"lat":1e999,"lon":1,"label":"ул Ленина 5"}'])
        await feed({"message": _msg(uid, web_app_data={"data": payload, "button_text": "x"})})
    elif kind == "location":
        await feed({"message": _msg(uid, location={"latitude": rnd.choice([55.7, 0.0, 89.9, -33.0, 55.97]),
                                                   "longitude": rnd.choice([37.6, 0.0, 179.0, 151.0, 37.4])})})
    else:
        await say(ADMIN, rnd.choice(CMDS))


async def main(seed: int, steps: int):
    await db.init_db()
    rnd = random.Random(seed)
    for uid in CLIENTS + [EXEC]:
        await say(uid, "/start")
        await callback(uid, "pdn:agree")
    await say(ADMIN, "/start")
    await say(ADMIN, "/add_executor Мария | +79005554433 | self_employed | walk,sitter,boarding,taxi | Химки | 70% | 2000")
    for i in range(steps):
        uid = rnd.choice(CLIENTS + [EXEC])
        try:
            await step(rnd, uid)
        except Exception as e:  # исключение вне хендлеров (в самом фреймворке)
            PROBLEMS.append(f"ИСКЛЮЧЕНИЕ вне хендлера на шаге {i}: {e!r}")
        if i % 400 == 0:
            await process_due_reminders(bot)
        if i % 250 == 0:  # админ двигает настоящие заказы по статусам
            row = await db.fetch_one("SELECT id, status FROM orders ORDER BY RANDOM() LIMIT 1")
            if row:
                oid = row["id"]
                await say(ADMIN, rnd.choice([f"/confirm {oid}", f"/send_payment {oid} https://pay.example/{oid}", f"/paid {oid}",
                                              f"/assign {oid} 1", f"/progress {oid}", f"/done {oid}", f"/cancel {oid} тест"]))
    # итоговая целостность БД
    bad = await db.fetch_all("SELECT id, status FROM orders WHERE status NOT IN "
                             "('created','confirmed','paid','assigned','in_progress','completed','cancelled')")
    if bad:
        PROBLEMS.append(f"заказы с неверным статусом: {bad}")
    orph = await db.fetch_all("SELECT o.id FROM orders o LEFT JOIN users u ON u.id=o.user_id WHERE u.id IS NULL")
    if orph:
        PROBLEMS.append(f"заказы без клиента: {orph}")
    import json
    for o in await db.fetch_all("SELECT * FROM orders"):
        oid = o["id"]
        logs = await db.fetch_all("SELECT to_status FROM order_statuses_log WHERE order_id=? ORDER BY id", (oid,))
        if not logs or logs[-1]["to_status"] != o["status"]:
            PROBLEMS.append(f"заказ {oid}: статус {o['status']} не совпал с журналом {[x['to_status'] for x in logs][-2:]}")
        bd = json.loads(o["price_breakdown"] or "[]")
        if sum(a for _, a in bd) != o["price_total"]:
            PROBLEMS.append(f"заказ {oid}: разбивка {bd} не даёт итог {o['price_total']}")
        if o["price_total"] <= 0:
            PROBLEMS.append(f"заказ {oid}: цена {o['price_total']} ≤ 0")
        if o["executor_id"] and o["margin"] != o["price_total"] - (o["executor_pay"] or 0):
            PROBLEMS.append(f"заказ {oid}: маржа {o['margin']} ≠ {o['price_total']} − {o['executor_pay']}")
        pay = await db.fetch_all("SELECT status FROM payments WHERE order_id=?", (oid,))
        if len(pay) != 1:
            PROBLEMS.append(f"заказ {oid}: платежей {len(pay)}")
        elif o["status"] in ("paid", "assigned", "in_progress", "completed") and pay[0]["status"] != "paid":
            PROBLEMS.append(f"заказ {oid}: статус {o['status']}, а платёж {pay[0]['status']}")
        if o["status"] in ("assigned", "in_progress") and not o["executor_id"]:
            PROBLEMS.append(f"заказ {oid}: {o['status']} без исполнителя")
        if o["end_at"] and o["end_at"] < o["start_at"]:
            PROBLEMS.append(f"заказ {oid}: конец раньше начала")
    dup = await db.fetch_all("SELECT order_id, kind, COUNT(*) n FROM reminders GROUP BY order_id, kind HAVING n>1")
    if dup:
        PROBLEMS.append(f"дубли напоминаний: {dup[:3]}")
    n_orders = (await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"]
    n_pets = (await db.fetch_one("SELECT COUNT(*) n FROM pets"))["n"]
    uniq = sorted({p.split("\n")[0][:150] for p in PROBLEMS})
    print(f"seed={seed} шагов={steps} сообщений бота={len(SENT)} заказов={n_orders} питомцев={n_pets}")
    print(f"ПРОБЛЕМ: {len(PROBLEMS)}, уникальных: {len(uniq)}")
    for u in uniq:
        print(" -", u)
    first = {}
    for p in PROBLEMS:
        first.setdefault(p.split("\n")[0][:150], p)
    for k, v in list(first.items())[:6]:
        print("\n---", k, "\n", v[-600:])
    sys.exit(1 if PROBLEMS else 0)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 1, int(sys.argv[2]) if len(sys.argv) > 2 else 3000))
