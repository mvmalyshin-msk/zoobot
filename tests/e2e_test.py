"""Сквозной тест без Telegram: подменяем сетевой слой aiogram и «нажимаем» кнопки/пишем сообщения.

Запуск из папки проекта:
    BOT_TOKEN=1:x ADMIN_IDS=111 DB_PATH=/tmp/e2e.db python tests/e2e_test.py
"""
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("ADMIN_IDS", "111")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_e2e.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + suffix)
    except FileNotFoundError:
        pass

from aiogram import Bot  # noqa: E402
from aiogram.client.session.base import BaseSession  # noqa: E402
from aiogram.exceptions import TelegramForbiddenError  # noqa: E402
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage, SendPhoto  # noqa: E402
from aiogram.types import Chat, Message  # noqa: E402

import db  # noqa: E402
import geo  # noqa: E402
from bot import build_dispatcher  # noqa: E402
from scheduler import process_due_reminders  # noqa: E402

CLIENT, ADMIN, EXEC = 1000, 111, 2000

# --- Подмена карты: настоящую сеть в тестах не трогаем ---
MAP_DOWN = False
FAKE_PLACES = {
    "ленина": [geo.Place("ул. Ленина, 1, Москва", "Москва", 55.75, 37.6, False, True),
               geo.Place("ул. Ленина, 1, Химки", "Химки", 55.9, 37.4, True, True)],
    "шереметьево": [geo.Place("Москва, Шереметьево, терминал B", "Химки", 55.97, 37.41, True, True)],
}


async def fake_search(text):
    if MAP_DOWN:
        return None
    for key, places in FAKE_PLACES.items():
        if key in text.lower():
            return places
    return []


async def fake_reverse(lat, lon):
    return None  # карта «не отвечает» на обратный поиск: район и МКАД определяются по координатам


DIAG = {"троицк": geo.Diagnosis(where="Троицк, Челябинская область", town="Троицк"),
        "минск": geo.Diagnosis(where="Минск, Беларусь", town=None),
        "зеленоград": geo.Diagnosis(where=None, town="Зеленоград")}


async def fake_diagnose(text):
    for key, d in DIAG.items():
        if key in text.lower():
            return d
    return geo.Diagnosis(None, None)


geo.search, geo.reverse, geo.diagnose = fake_search, fake_reverse, fake_diagnose
SENT: list[dict] = []  # всё, что бот «отправил»
BLOCKED: set[int] = set()  # кто «заблокировал бота»
ANSWERS: list[str] = []  # всплывающие ответы на кнопки (answerCallbackQuery)


class FakeSession(BaseSession):
    async def close(self): ...
    async def stream_content(self, *a, **kw): ...

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            if method.chat_id in BLOCKED:
                raise TelegramForbiddenError(method=method, message="Forbidden: bot was blocked by the user")
            markup = method.reply_markup
            SENT.append({"to": method.chat_id, "text": method.text, "markup": markup})
            return Message(message_id=len(SENT), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=method.text)
        if isinstance(method, SendPhoto):
            SENT.append({"to": method.chat_id, "text": method.caption or "", "markup": method.reply_markup, "photo": True})
            return Message(message_id=len(SENT), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"))
        if isinstance(method, SendDocument):
            f = method.document
            SENT.append({"to": method.chat_id, "text": method.caption or "", "markup": None, "document": f.filename,
                         "data": f.data})
            return Message(message_id=len(SENT), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"))
        if isinstance(method, EditMessageText):  # окно календаря меняется на месте
            SENT.append({"to": method.chat_id, "text": method.text, "markup": method.reply_markup, "edited": True})
            return True
        if isinstance(method, AnswerCallbackQuery) and method.text:
            ANSWERS.append(method.text)
        return True


bot = Bot("1:x", session=FakeSession())
dp = build_dispatcher()

_uid = 0


def _user(uid):
    return {"id": uid, "is_bot": False, "first_name": f"U{uid}", "username": f"u{uid}"}


async def say(uid: int, text: str) -> list[dict]:
    """Пользователь пишет сообщение. Возвращает ответы бота."""
    global _uid
    _uid += 1
    start = len(SENT)
    msg = {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
           "from": _user(uid), "text": text}
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    await dp.feed_raw_update(bot, {"update_id": _uid, "message": msg})
    return SENT[start:]


async def photo(uid: int, caption: str = "") -> list[dict]:
    global _uid
    _uid += 1
    start = len(SENT)
    msg = {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"}, "from": _user(uid),
           "photo": [{"file_id": "f", "file_unique_id": "u", "width": 5, "height": 5}]}
    if caption:
        msg["caption"] = caption
    await dp.feed_raw_update(bot, {"update_id": _uid, "message": msg})
    return SENT[start:]


async def contact(uid: int, phone: str) -> list[dict]:
    global _uid
    _uid += 1
    start = len(SENT)
    msg = {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
           "from": _user(uid), "contact": {"phone_number": phone, "first_name": "x", "user_id": uid}}
    await dp.feed_raw_update(bot, {"update_id": _uid, "message": msg})
    return SENT[start:]


def _buttons(m):
    if not m["markup"] or not hasattr(m["markup"], "inline_keyboard"):
        return []
    return [b for row in m["markup"].inline_keyboard for b in row]


async def click(uid: int, label: str) -> list[dict]:
    """Нажать inline-кнопку, чей текст содержит label (ищем в последних сообщениях бота этому пользователю)."""
    global _uid
    for m in reversed([s for s in SENT if s["to"] == uid]):
        for b in _buttons(m):
            if label in b.text and b.callback_data:
                _uid += 1
                start = len(SENT)
                cb = {"id": str(_uid), "from": _user(uid), "chat_instance": "ci", "data": b.callback_data,
                      "message": {"message_id": _uid, "date": int(time.time()),
                                  "chat": {"id": uid, "type": "private"},
                                  "from": {"id": 999, "is_bot": True, "first_name": "bot"}, "text": m["text"]}}
                await dp.feed_raw_update(bot, {"update_id": _uid, "callback_query": cb})
                return SENT[start:]
    raise AssertionError(f"Кнопка «{label}» не найдена у {uid}")


async def raw_callback(uid: int, data: str) -> list[dict]:
    """Нажатие кнопки с произвольным callback_data (в том числе «битым»)."""
    global _uid
    _uid += 1
    start = len(SENT)
    cb = {"id": str(_uid), "from": _user(uid), "chat_instance": "ci", "data": data,
          "message": {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
                      "from": {"id": 999, "is_bot": True, "first_name": "bot"}, "text": "x"}}
    await dp.feed_raw_update(bot, {"update_id": _uid, "callback_query": cb})
    return SENT[start:]


async def web_app_pick(uid: int, payload: dict) -> list[dict]:
    """Клиент выбрал адрес в мини-приложении с картой (Telegram присылает web_app_data)."""
    global _uid
    _uid += 1
    start = len(SENT)
    msg = {"message_id": _uid, "date": int(time.time()), "chat": {"id": uid, "type": "private"},
           "from": _user(uid), "web_app_data": {"data": json.dumps(payload), "button_text": "Найти на карте"}}
    await dp.feed_raw_update(bot, {"update_id": _uid, "message": msg})
    return SENT[start:]


async def svc(uid: int, label: str) -> list[dict]:
    """Выбор услуги в два шага: сначала одна из четырёх услуг, потом вариант внутри (у такси вариант один)."""
    head, _, rest = label.partition(" ")
    r = await click(uid, head)
    if not rest:
        return r
    return await click(uid, rest[:1].upper() + rest[1:])


async def taxi_pre(uid: int, pet: str, rider: str = "Для меня") -> list[dict]:
    """Шаги перед выбором времени у зоотакси: какой питомец едет, предупреждение, для кого заказ."""
    await click(uid, pet)
    await click(uid, "Понял")
    return await click(uid, rider)


async def edit_click(uid: int, label: str) -> list[dict]:
    """Правка заказа: сначала «Внести правки», потом нужная кнопка."""
    await click(uid, "Внести правки")
    return await click(uid, label)


def last_text(msgs, to=None):
    msgs = [m for m in msgs if to is None or m["to"] == to]
    return msgs[-1]["text"] if msgs else ""


def ok(cond, title):
    print(("  ✅ " if cond else "  ❌ ") + title)
    if not cond:
        raise AssertionError(title)


async def main():
    await db.init_db()
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%d.%m.%Y")  # для проверки формата даты

    print("1. Согласие на ПДн")
    r = await say(CLIENT, "/start")
    ok("Согласен на обработку ПДн" in str(_buttons(r[-1])), "экран согласия с отдельной кнопкой")
    r = await say(CLIENT, "/menu")
    ok("согласие" in last_text(r).lower(), "без согласия дальше не пускает")
    r = await click(CLIENT, "Согласен")
    c = await db.fetch_one("SELECT * FROM consents WHERE type='pdn'")
    ok(c and c["granted"] == 1 and c["text_version"], "согласие записано в consents с версией текста")
    u = await db.fetch_one("SELECT * FROM users")
    ok(u["marketing_consent"] == 0, "согласие на рассылки по умолчанию выключено")

    print("2. Профиль при первом заказе")
    await click(CLIENT, "Заказать")
    r = await say(CLIENT, "ааа")
    ok("не похоже на имя" in last_text(r).lower(), "мусорное имя отклоняется")
    await say(CLIENT, "Анна")
    r = await say(CLIENT, "abc")
    ok("не получилось" in last_text(r).lower(), "неверный телефон отклоняется")
    await contact(CLIENT, "79001234567")
    r = await say(CLIENT, "Химки")
    ok("не похоже на адрес" in last_text(r).lower(), "адрес без улицы и дома отклоняется")
    r = await say(CLIENT, "ывфвф 5")
    ok("не похоже на адрес" in last_text(r).lower(), "клавиатурная каша вместо адреса отклоняется")
    r = await say(CLIENT, "абракадабра 9")
    ok("не нашёл" in last_text(r).lower() and "Оставить как написано" not in str(_buttons(r[-1])),
       "несуществующий адрес: просит переписать, «оставить как есть» ещё нет")
    r = await say(CLIENT, "абракадабра 9")
    ok("Оставить как написано" in str(_buttons(r[-1])), "после второй неудачи появляется «оставить как написано»")
    r = await say(CLIENT, "троицк садовая 3")
    ok("Челябинская область" in last_text(r) and "Москве и Московской области" in last_text(r),
       "адрес из другого региона: бот называет, где он, и что это вне зоны работы")
    r = await say(CLIENT, "троицк пушкина 3")
    ok("Оставить как написано" not in str(_buttons(r[-1])), "адрес вне региона: «оставить как написано» не предлагается даже после двух неудач")
    n_users_addr = (await db.fetch_one("SELECT address FROM users"))["address"]
    await raw_callback(CLIENT, "ad:keep")
    ok((await db.fetch_one("SELECT address FROM users"))["address"] == n_users_addr,
       "устаревшая кнопка «оставить как написано» не сохраняет адрес из другого региона")
    r = await say(CLIENT, "зеленоград новая 9")
    ok("Зеленоград" in last_text(r) and "улиц" in last_text(r), "найден только город: бот просит уточнить улицу и дом")
    r = await say(CLIENT, "ул. Ленина, 1, кв. 5")
    ok(len(_buttons(r[-1])) >= 3 and "Ленина" in str(_buttons(r[-1])), "адрес найден: варианты кнопками")
    r = await click(CLIENT, "Ленина, 1, Москва")
    ok("Как вам удобнее" in last_text(r) and "Звонок" in str(_buttons(r[-1])), "после адреса — вопрос о способе связи")
    r = await click(CLIENT, "Звонок")
    ok("Как его зовут" in last_text(r), "после выбора способа связи — вопрос о питомце")
    ok((await db.fetch_one("SELECT contact_pref FROM users"))["contact_pref"] == "call", "способ связи сохранён")
    u = await db.fetch_one("SELECT * FROM users")
    ok(u["address"] == "ул. Ленина, 1, Москва, кв. 5" and u["district"] == "Москва" and u["address_verified"] == 1
       and u["outside_mkad"] == 0, "адрес, район и «в пределах МКАД» взяты с карты, квартира добавлена")
    r = await say(CLIENT, "говно")
    ok("не похоже на кличку" in last_text(r).lower(), "мат вместо клички отклоняется")
    r = await say(CLIENT, "ывфвф")
    ok("не похоже на кличку" in last_text(r).lower(), "набор букв вместо клички отклоняется")
    await say(CLIENT, "Рекс")
    r = await click(CLIENT, "Собака")
    r = await say(CLIENT, "ывфвф")
    ok("не похоже на название породы" in last_text(r).lower(), "несуществующая порода отклоняется")
    r = await say(CLIENT, "лабродор")
    ok("Лабрадор" in str(_buttons(r[-1])), "опечатка в породе: бот предлагает «Лабрадор-ретривер»")
    r = await click(CLIENT, "Лабрадор")
    ok("Размер питомца" in last_text(r), "после породы — размер")
    r = await say(CLIENT, "ыыыы")
    ok("S, M, L" in last_text(r), "на мусор вместо размера бот подсказывает варианты")
    r = await say(CLIENT, "большой")
    ok("Особенности" in last_text(r), "слово «большой» понято как размер L; дальше — анкета повадок")
    r = await click(CLIENT, "Готово")
    ok("Здоровье" in last_text(r), "после повадок — здоровье и аллергии")
    r = await click(CLIENT, "Ничего")
    pet = await db.fetch_one("SELECT * FROM pets")
    ok(pet["name"] == "Рекс" and pet["breed"] == "Лабрадор-ретривер" and pet["size"] == "L", "питомец сохранён с проверенной породой")
    ok("Выберите услугу" in last_text(r), "после профиля сразу выбор услуги")
    u = await db.fetch_one("SELECT * FROM users")
    ok(u["phone"] == "+79001234567" and u["address"].startswith("ул. Ленина"), "профиль сохранён")

    print("2б. Ещё питомцы: другое животное, чужая порода, редкая порода")
    await say(CLIENT, "/profile")
    await click(CLIENT, "Питомцы")
    await click(CLIENT, "Добавить")
    await say(CLIENT, "Пуша")
    await click(CLIENT, "Другое")
    r = await say(CLIENT, "дракон")
    ok("Оставить как написано" in str(_buttons(r[-1])), "«дракон»: не из списка домашних, можно оставить, админ проверит")
    r = await say(CLIENT, "хомяг")
    ok("Хомяк" in str(_buttons(r[-1])), "опечатка в названии животного: предлагает «Хомяк»")
    await click(CLIENT, "Хомяк")
    await click(CLIENT, "S")
    await click(CLIENT, "Ничего")
    pet = await db.fetch_one("SELECT * FROM pets WHERE name='Пуша'")
    ok(pet["species"] == "хомяк" and pet["size"] == "S", "вид сохранён конкретный: «хомяк»")
    await click(CLIENT, "Добавить")
    await say(CLIENT, "Мурка")
    await click(CLIENT, "Кошка")
    r = await say(CLIENT, "лабрадор")
    ok("это собака" in last_text(r) and "Собака" in str(_buttons(r[-1])), "порода собаки у кошки: бот возвращает к выбору вида")
    await click(CLIENT, "Кошка")
    r = await say(CLIENT, "тосса-ину")
    ok("Оставить как написано" in str(_buttons(r[-1])), "редкая настоящая порода: можно оставить")
    await click(CLIENT, "Оставить как написано")
    await click(CLIENT, "M")
    await click(CLIENT, "Ничего")
    pet = await db.fetch_one("SELECT * FROM pets WHERE name='Мурка'")
    ok(pet["breed"] == "Тосса-ину" and "уточнить" in (pet["notes"] or ""), "непроверенная порода помечена для администратора")
    await db.execute("UPDATE pets SET is_active=0 WHERE name IN ('Пуша','Мурка')")

    print("3. Выгул 60 мин ночью: расчёт цены")
    await svc(CLIENT, "Выгул 60")
    r = await say(CLIENT, "послезавтра 23:30")
    ok("с 06:00 до 22:00" in last_text(r), "ночью (вне 06:00–22:00) заказ не принимается")
    r = await say(CLIENT, "послезавтра 06:30")  # рабочие часы, но ещё «ночной» тариф (до 07:00)
    txt = last_text(r)
    # (700 + 100 за крупную собаку) × 1.3 ночью = 1040
    ok("1 040 ₽" in txt and "Крупная собака" in txt and "Ночное" in txt, "разбивка: база + L + ночь = 1 040 ₽")
    r = await edit_click(CLIENT, "За МКАД")
    ok("(700 + 100 + 150) × 1.3" or "1 235 ₽" in last_text(r), "за МКАД пересчитывает")
    ok("1 235 ₽" in last_text(r), "итог за МКАД = 1 235 ₽")
    await edit_click(CLIENT, "За МКАД")
    r = await click(CLIENT, "Подтвердить")
    ok("принята" in last_text(r, CLIENT), "клиент получил подтверждение заявки")
    ok(any(m["to"] == ADMIN and "Новая заявка" in m["text"] for m in r), "админ получил уведомление")
    o = await db.fetch_one("SELECT * FROM orders WHERE id=1")
    ok(o["status"] == "created" and o["price_total"] == 1040, "заказ создан, цена зафиксирована")

    print("4. Статусы, оплата, исполнитель (админ)")
    r = await say(ADMIN, "/confirm 1")
    ok(any(m["to"] == CLIENT and "подтверждён" in m["text"] for m in r), "created → confirmed, клиент уведомлён")
    r = await say(ADMIN, "/progress 1")
    ok("Нельзя" in last_text(r, ADMIN), "перескочить confirmed → in_progress нельзя")
    r = await say(ADMIN, "/send_payment 1 https://pay.example/1")
    p = await db.fetch_one("SELECT * FROM payments WHERE order_id=1")
    ok(p["status"] == "link_sent" and p["payment_link"], "платёж: pending → link_sent, ссылка в БД")
    ok(any(m["to"] == CLIENT and "оплаты" in m["text"] for m in r), "клиент получил ссылку")
    rem = await db.fetch_one("SELECT * FROM reminders WHERE kind='pay_3h'")
    ok(rem is not None, "напоминание об оплате через 3 ч запланировано")
    # перематываем время: делаем напоминание «созревшим»
    await db.execute("UPDATE reminders SET send_at=datetime('now','-1 minute')")
    n = await process_due_reminders(bot)
    ok(any("ещё не оплачен" in m["text"] for m in SENT if m["to"] == CLIENT), "клиент получил напоминание об оплате")
    r = await say(ADMIN, "/paid 1")
    p = await db.fetch_one("SELECT * FROM payments WHERE order_id=1")
    ok(p["status"] == "paid", "платёж: link_sent → paid")
    kinds = {x["kind"] for x in await db.fetch_all("SELECT kind FROM reminders WHERE order_id=1")}
    ok({"client_24h", "client_2h"} <= kinds, "запланированы напоминания клиенту за 24 ч и за 2 ч")

    r = await say(ADMIN, "/add_executor Мария | +79005554433 | self_employed | walk,sitter | Химки | 70% | 2000")
    ok("добавлен" in last_text(r), "исполнитель добавлен")
    r = await say(ADMIN, "/add_executor Петя | +79001112233 | individual | walk | Химки | 300 | 2001 | " +
                  str(datetime.now().year - 15) + " | 0")
    ok("согласие родителей" in last_text(r), "15-летний без согласия родителей отклонён")
    yr = datetime.now().year
    r = await say(ADMIN, f"/add_executor Вася | +79001112244 | individual | walk | Химки | 300 | 2002 | {yr - 14} | 1")
    ok("младше 14" in last_text(r), "родился в год «текущий − 14»: возможно, ему ещё 13 — отклонён (год без даты считаем осторожно)")
    r = await say(ADMIN, "/add_executor Коля | +79001112255 | individual | walk | Химки | 300 | 2003")
    ok("год рождения" in last_text(r).lower(), "физлицо без года рождения не добавляется (возраст неизвестен)")
    r = await say(ADMIN, f"/add_executor Лена | +79001112266 | individual | walk | Химки | 300 | 2004 | {yr - 16} | 1")
    ok("добавлен" in last_text(r), "физлицо 15–17 лет с согласием родителей добавлено")
    r = await say(ADMIN, "/assign 1 1")
    txt = last_text(r, ADMIN)
    ok("Ваша маржа: 312 ₽" in txt and "Вознаграждение: 728 ₽" in txt, "маржа: 1040 − 70% = 312 ₽ → " + txt.splitlines()[-1])
    ok(any(m["to"] == EXEC and "Вам назначен заказ" in m["text"] for m in r), "исполнитель уведомлён")
    ok(any(m["to"] == CLIENT and "Мария" in m["text"] for m in r), "клиент уведомлён о назначении")
    await db.execute("UPDATE reminders SET send_at=datetime('now','-1 minute')")
    await process_due_reminders(bot)
    ok(any(m["to"] == EXEC and "Через 2 часа" in m["text"] for m in SENT), "исполнитель получил напоминание за 2 ч")
    ok(any(m["to"] == CLIENT and "завтра в 06:30" in m["text"] for m in SENT), "клиент получил напоминание за 24 ч")

    print("5. Исполнитель ведёт заказ, клиент оставляет отзыв")
    await click(EXEC, "Начал")
    o = await db.fetch_one("SELECT status FROM orders WHERE id=1")
    ok(o["status"] == "in_progress", "assigned → in_progress (кнопкой исполнителя)")
    r = await click(EXEC, "Завершил")
    ok(any(m["to"] == CLIENT and "Оцените" in m["text"] for m in r), "completed → клиенту запрос оценки")
    ok(any(m["to"] == EXEC and "отчёт" in m["text"].lower() and "Готово" in str(_buttons(m)) for m in r),
       "после выгула исполнителю предложено отправить отчёт")
    r = await say(EXEC, "Гуляли в парке, настроение бодрое, большие дела в норме")
    ok(any(m["to"] == CLIENT and "Отчёт о выгуле" in m["text"] for m in r) and any(m["to"] == CLIENT and "в парке" in m["text"] for m in r), "отчёт (заголовок и текст) ушёл клиенту")
    r = await photo(EXEC, "Рекс в парке")
    ok(any(m["to"] == CLIENT and m.get("photo") and "Рекс в парке" in m["text"] for m in r), "фото с подписью ушло клиенту")
    r = await click(EXEC, "Готово")
    ok(any(m["to"] == EXEC and "Спасибо" in m["text"] for m in r), "исполнитель закончил отчёт")
    r = await say(EXEC, "это уже не отчёт")
    ok(not any(m["to"] == CLIENT for m in r), "после «Готово» сообщения исполнителя клиенту не пересылаются")
    await click(CLIENT, "⭐⭐⭐⭐⭐")
    r = await say(CLIENT, "Всё отлично, спасибо!")
    rv = await db.fetch_one("SELECT * FROM reviews")
    ok(rv and rv["rating"] == 5 and rv["executor_id"] == 1, "отзыв сохранён")
    r = await say(ADMIN, "/executors")
    ok("⭐ 5.0 (1)" in last_text(r), "средний рейтинг в /executors")

    print("6. Повтор заказа, такси, зооняня")
    r = await click(CLIENT, "Повторить") if any("Повторить" in str(_buttons(m)) for m in SENT if m["to"] == CLIENT) else await say(CLIENT, "/menu")
    if "Повторяем" not in last_text(r):
        await click(CLIENT, "Повторить")
    r = await say(CLIENT, "послезавтра 10:00")
    ok("Проверьте заказ" in last_text(r) and "Рекс" in last_text(r), "повтор: спросил только время, остальное из профиля")
    await click(CLIENT, "Отмена")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Зооняня приходящая")
    await say(CLIENT, "через неделю")
    r = await say(CLIENT, "01.01.2020 10:00")
    ok("Самое раннее" in last_text(r), "дата в прошлом отклоняется с подсказкой самого раннего времени")
    r = await say(CLIENT, "01.01.2099 10:00")
    ok("далеко" in last_text(r), "дата через десятилетия (опечатка в годе) отклоняется")
    future = (datetime.now() + timedelta(days=3)).strftime("%d.%m %H:%M")
    r = await say(CLIENT, future.replace(future[-5:], "11:00"))
    r = await click(CLIENT, "3")
    ok("3 ч × 600 ₽" in last_text(r) and "1 900 ₽" in last_text(r), "зооняня 3 ч: 1 800 + 100 за крупную = 1 900 ₽")
    await click(CLIENT, "Отмена")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Зоотакси")
    await taxi_pre(CLIENT, "Рекс")
    await say(CLIENT, future.replace(future[-5:], "12:00"))
    await click(CLIENT, "📍")
    r = await say(CLIENT, "Москва, Шереметьево, терминал B")
    ok("Москва, Шереметьево, терминал B" in str(_buttons(r[-1])), "точка Б: тоже варианты с карты")
    r = await click(CLIENT, "Шереметьево")
    t = last_text(r)
    ok("Откуда: ул. Ленина" in t and "Куда: Москва" in t, "такси: точки А и Б на экране подтверждения")
    ok("за мкад" in last_text(r).lower() and "Ориентировочная" in last_text(r), "такси в аэропорт: «за МКАД» определено по карте само")
    await click(CLIENT, "Подтвердить")
    ok(await db.fetch_one("SELECT 1 FROM orders WHERE address_to IS NOT NULL") is not None, "заказ такси создан с точкой Б")
    r = await say(ADMIN, "/cancel 2 клиент передумал")
    ok("Отменён" in last_text(r, ADMIN), "админ отменил заказ")
    ok(any(m["to"] == CLIENT and "отменён" in m["text"] for m in r), "клиент уведомлён об отмене")
    r = await say(ADMIN, "/confirm 2")
    ok("Нельзя" in last_text(r, ADMIN), "из cancelled назад нельзя")

    print("7. Отмена клиентом, чужие данные")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Выгул 30")
    await say(CLIENT, future.replace(future[-5:], "15:00"))
    global MAP_DOWN
    MAP_DOWN = True
    await edit_click(CLIENT, "Изменить адрес")
    r = await say(CLIENT, "ул. Новая, 7")
    ok("Карта сейчас не отвечает" in str([m["text"] for m in r]), "карта недоступна: адрес принят, клиент предупреждён")
    MAP_DOWN = False
    r = await click(CLIENT, "Подтвердить")
    ok(any(m["to"] == ADMIN and "Адрес не подтверждён" in m["text"] for m in r),
       "админу в заявке видно «адрес не подтверждён»")
    await click(CLIENT, "Мои заказы")
    await click(CLIENT, "Отменить №3")
    r = await click(CLIENT, "Да, отменить")
    o = await db.fetch_one("SELECT status FROM orders WHERE id=3")
    ok(o["status"] == "cancelled", "клиент сам отменил неоплаченный заказ")
    ok(any(m["to"] == ADMIN and "client:" in m["text"] and "Отменён" in m["text"] for m in r), "админ узнал об отмене клиентом")

    print("8. Админка: цены, правила, статистика, рассылка")
    r = await say(ADMIN, "/price_set 2 800")
    ok("800" in last_text(r), "/price_set меняет цену без правки кода")
    await say(ADMIN, "/rule_add 0 holiday percent 20 Праздничный день тест")
    rules = await db.fetch_all("SELECT * FROM price_rules WHERE active=1")
    ok(len(rules) == 6, "/rule_add добавил правило")
    r = await say(ADMIN, "/rule_add 0 nonsense fixed 10 Плохое")
    ok("Условия:" in last_text(r), "неизвестное условие отклоняется")
    await say(ADMIN, "/rule_del 6")
    ok(len(await db.fetch_all("SELECT * FROM price_rules WHERE active=1")) == 5, "/rule_del отключил правило")
    r = await say(ADMIN, "/stats")
    ok("Заявок: 3" in last_text(r) and "Отмен: 2" in last_text(r), "/stats: " + last_text(r).replace("\n", " | ")[:160])
    r = await say(ADMIN, "/orders")
    ok("Активных заказов нет" in last_text(r) or "№" in last_text(r), "/orders работает")
    r = await say(ADMIN, "/broadcast Скидка 10% на выгул")
    ok("получателей с согласием: 0" in last_text(r), "рассылка без согласий уходит 0 получателям")
    await say(CLIENT, "/profile")
    await click(CLIENT, "Настройки")
    await click(CLIENT, "Получать акции")
    r = await say(ADMIN, "/broadcast Скидка 10% на выгул")
    ok("доставлено 1" in last_text(r), "после согласия рассылка доходит")
    ok(any(m["to"] == CLIENT and "Скидка 10%" in m["text"] for m in SENT), "клиент получил рассылку")
    log_n = (await db.fetch_one("SELECT COUNT(*) n FROM admin_log"))["n"]
    ok(log_n >= 15, f"действия админа в admin_log ({log_n} записей)")
    r = await say(CLIENT, "/orders")
    ok("Что нужно вашему питомцу" in last_text(r), "обычному клиенту админ-команды недоступны")

    bad = await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
                             "('schema_version','holidays','kv','sqlite_sequence') AND name NOT LIKE 'sqlite_%'")
    for t in bad:
        cols = [c["name"] for c in await db.fetch_all(f"PRAGMA table_info({t['name']})")]
        ok("updated_at" in cols, f"updated_at есть в {t['name']}")
    print("9. Устойчивость к «битым» кнопкам")
    for bad_data in ("svc:abc", "ex:x", "ex:a:b:c", "cancel:x", "qty:"):
        n = len(ANSWERS)
        await raw_callback(CLIENT, bad_data)
        ok(len(ANSWERS) > n and ("не так" in ANSWERS[-1] or "устарела" in ANSWERS[-1]), f"битая кнопка «{bad_data}» не оставляет клиента без ответа")

    print("10. Клиент заблокировал бота: админ должен об этом узнать")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Выгул 30")
    await say(CLIENT, future.replace(future[-5:], "16:00"))
    await click(CLIENT, "Подтвердить")
    n = (await db.fetch_one("SELECT MAX(id) n FROM orders"))["n"]
    BLOCKED.add(CLIENT)
    r = await say(ADMIN, f"/confirm {n}")
    ok("не получил" in last_text(r, ADMIN), "админ видит: клиент не получил уведомление о статусе")
    r = await say(ADMIN, f"/send_payment {n} https://pay.example/blocked")
    ok("не получил" in last_text(r, ADMIN), "админ видит: клиент не получил ссылку на оплату")
    BLOCKED.discard(CLIENT)

    print("11. Двойное нажатие «Подтвердить» не создаёт два заказа")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Выгул 30")
    await say(CLIENT, future.replace(future[-5:], "17:00"))
    before = (await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"]
    await asyncio.gather(click(CLIENT, "Подтвердить"), click(CLIENT, "Подтвердить"))
    after = (await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"]
    ok(after == before + 1, f"двойной тап: создан один заказ (было {before}, стало {after})")

    print("12. Адрес из мини-приложения с картой")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Выгул 30")
    r = await say(CLIENT, future.replace(future[-5:], "18:00"))
    await edit_click(CLIENT, "Изменить адрес")
    r = await web_app_pick(CLIENT, {"lat": 59.9, "lon": 30.3, "label": "Невский проспект, 1", "flat": ""})
    ok("не получилось" in last_text(r).lower(), "точка вне Москвы и области отклоняется")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "ывфвф 5", "flat": ""})
    ok("не получилось" in last_text(r).lower(), "мусорный текст адреса из карты отклоняется")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "Зеленоград, корпус 1105", "flat": "12"})
    ok("Проверьте адрес" in last_text(r) and "Зеленоград, корпус 1105, кв. 12" in last_text(r)
       and "Проверьте заказ" not in last_text(r), "адрес с карты сначала показан клиенту на проверку")
    ok(any("Ввести заново" in b.text for b in _buttons(r[-1])), "на проверке адреса можно сразу ввести заново")
    r = await click(CLIENT, "Зеленоград, корпус 1105")
    t = last_text(r)
    ok("Адрес: Зеленоград, корпус 1105, кв. 12" in t, "после подтверждения адрес с карты и квартира на экране заказа")
    ok("за мкад" in last_text(r).lower() and "Ориентировочная" in last_text(r), "«за МКАД» определено по координатам точки")
    r = await click(CLIENT, "Подтвердить")
    o = await db.fetch_one("SELECT * FROM orders ORDER BY id DESC LIMIT 1")
    ok(o["address_unverified"] == 0 and o["outside_mkad"] == 1, "заказ: адрес подтверждён картой, за МКАД")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "Зеленоград 1", "flat": ""})
    ok(not any("Проверьте заказ" in m["text"] for m in r), "картой вне диалога адреса ничего не меняется")


    print("13. Выбор даты и времени кнопками")
    from config import config as _cfg
    day = (datetime.now(_cfg.tz) + timedelta(days=3)).date().isoformat()
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    r = await svc(CLIENT, "Выгул 30")
    ok("календар" in last_text(r) and any(b.callback_data.startswith("cal:d:") for b in _buttons(r[-1])),
       "после выбора услуги показан календарь")
    n_orders = (await db.fetch_one("SELECT COUNT(*) c FROM orders"))["c"]
    r = await raw_callback(CLIENT, "cal:d:2026-13-40")
    ok(ANSWERS[-1].startswith("Не получилось"), "битая кнопка календаря не ломает бота")
    r = await raw_callback(CLIENT, "cal:t:2020-01-01:10:00")
    ok(ANSWERS[-1].startswith("Записываем не раньше"), "время в прошлом из календаря отклоняется")
    r = await raw_callback(CLIENT, f"cal:d:{day}")
    ok(f"cal:h:{day}:09" in str(_buttons(r[-1])) and r[-1].get("edited"), "день выбран → окно сменилось на часы")
    r = await raw_callback(CLIENT, f"cal:h:{day}:11")
    ok(f"cal:t:{day}:11:30" in str(_buttons(r[-1])), "час выбран → минуты")
    r = await raw_callback(CLIENT, f"cal:t:{day}:11:30")
    ok("Проверьте заказ" in last_text(r) and "11:30" in last_text(r), "время выбрано → экран подтверждения с этим временем")
    ok("Когда:" in r[0]["text"] and "11:30" in r[0]["text"], "окно календаря заменено на выбранное время")
    r = await raw_callback(CLIENT, f"cal:d:{day}")
    ok("уже не нужно" in ANSWERS[-1], "кнопка из старого окна ничего не ломает")
    await click(CLIENT, "Отмена")
    ok((await db.fetch_one("SELECT COUNT(*) c FROM orders"))["c"] == n_orders, "заказов от календаря не создано")


    print("14. Запись не раньше чем за сутки (кроме такси)")
    import texts as _texts
    _now = datetime.now(_cfg.tz)

    def _fmt(dt):
        return dt.strftime("%d.%m.%Y %H:%M")

    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    r = await svc(CLIENT, "Выгул 30")
    ok("за сутки" in last_text(r), "в вопросе про время сказано про сутки")
    today_btn = f"cal:d:{_now.date().isoformat()}"
    ok(today_btn not in str(_buttons(r[-1])), "в календаре выгула сегодня недоступно")
    r = await say(CLIENT, _fmt(_now + timedelta(hours=2)))
    ok("не раньше чем за сутки" in last_text(r) and "Проверьте заказ" not in last_text(r), "выгул через 2 часа не принимается")
    r = await say(CLIENT, _fmt(_now + timedelta(hours=23)))
    ok("не раньше чем за сутки" in last_text(r), "выгул через 23 часа не принимается")
    import datepicker as _dp
    r = await say(CLIENT, _fmt(_dp.first_allowed(_now + timedelta(hours=25))))  # ночью — ближайшее рабочее время
    ok("Проверьте заказ" in last_text(r), "выгул через 25 часов (в рабочие часы) принимается")
    await click(CLIENT, "Отмена")
    for name in ("Зооняня приходящая", "Передержка в частном доме"):  # другие услуги — то же правило
        await say(CLIENT, "/menu")
        await click(CLIENT, "Заказать")
        await svc(CLIENT, name)
        r = await say(CLIENT, _fmt(_now + timedelta(hours=5)))
        ok("не раньше чем за сутки" in last_text(r), f"{name}: через 5 часов не принимается")
        await click(CLIENT, "Отмена")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    r = await svc(CLIENT, "Зоотакси")
    r = await taxi_pre(CLIENT, "Рекс")
    ok("за сутки" not in last_text(r) and "машину" in last_text(r), "у такси про сутки не говорится")
    if _now + timedelta(minutes=30) <= _now.replace(hour=_dp.WORK_TO, minute=0, second=0, microsecond=0):
        ok(today_btn in str(_buttons(r[-1])), "в календаре такси сегодня доступно")  # пока не поздно (до 22:00)
    r = await say(CLIENT, _fmt(_dp.first_allowed(_now + timedelta(hours=1))))  # ночью — ближайшее рабочее время
    ok(any(_texts.ASK_POINT_A in m["text"] for m in r), "такси через час (в рабочие часы) принимается")
    r = await say(CLIENT, "01.01.2020 10:00")
    await click(CLIENT, "Отмена")


    print("15. Экран заказа: диапазон цены и вопрос «подбирать специалиста или внести правки»")
    day = (datetime.now(_cfg.tz) + timedelta(days=4)).strftime("%d.%m.%Y")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Выгул 60")
    r = await say(CLIENT, f"{day} 06:30")  # рабочие часы, «ночной» тариф до 07:00
    t = last_text(r)
    ok("Ориентировочная стоимость: от 1 170 ₽ до 1 350 ₽" in t, "цена показана диапазоном: от расчёта до +15 %")
    ok("Итого" not in t, "точный «Итого» на экране заказа не показывается")
    ok("Крупная собака" in t and "Ночное" in t, "что учтено в расчёте — видно")
    ok("подобрать специалиста" in t.lower() and "правки" in t, "вопрос: подбирать специалиста или внести правки")
    labels = " | ".join(b.text for b in _buttons(r[-1]))
    ok("Подтвердить" in labels and "Внести правки" in labels and "За МКАД" not in labels,
       "на первом экране две кнопки: подбирать / внести правки (правки спрятаны)")
    r = await click(CLIENT, "Внести правки")
    labels = " | ".join(b.text for b in _buttons(r[-1]))
    ok(all(x in labels for x in ("За МКАД", "Изменить адрес", "Изменить время", "Назад")), "в правках: МКАД, адрес, время, назад")
    r = await click(CLIENT, "За МКАД")
    ok("от 1 365 ₽ до 1 570 ₽" in last_text(r), "после правки диапазон пересчитан (1 365 → 1 570)")
    await click(CLIENT, "Внести правки")
    r = await click(CLIENT, "Назад")
    ok("Ориентировочная стоимость" in last_text(r), "«Назад» возвращает на экран заказа")
    await click(CLIENT, "Внести правки")
    r = await click(CLIENT, "Изменить время")
    ok("календар" in last_text(r), "«Изменить время» открывает календарь")
    day2 = (datetime.now(_cfg.tz) + timedelta(days=5)).strftime("%d.%m.%Y")
    r = await say(CLIENT, f"{day2} 10:00")
    ok("Проверьте заказ" in last_text(r) and day2 in last_text(r), "после смены времени — снова экран заказа с новой датой")
    n = (await db.fetch_one("SELECT COUNT(*) c FROM orders"))["c"]
    r = await click(CLIENT, "Подтвердить")
    ok("точную стоимость" in "".join(m["text"] for m in r), "после подтверждения сказано, что точную стоимость назовём после подбора")
    o = await db.fetch_one("SELECT * FROM orders ORDER BY id DESC LIMIT 1")
    ok((await db.fetch_one("SELECT COUNT(*) c FROM orders"))["c"] == n + 1 and o["price_total"] > 0, "заявка создана с расчётной ценой внутри")


    # у почасовой услуги смена времени не должна заново спрашивать количество часов
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await svc(CLIENT, "Зооняня приходящая")
    await say(CLIENT, f"{day} 11:00")
    r = await click(CLIENT, "3")
    ok("Проверьте заказ" in last_text(r) and "3 ч" in last_text(r), "зооняня: 3 часа, экран заказа")
    await click(CLIENT, "Внести правки")
    await click(CLIENT, "Изменить время")
    r = await say(CLIENT, f"{day2} 12:00")
    ok("Проверьте заказ" in last_text(r) and "3 ч" in last_text(r) and "Сколько часов" not in last_text(r),
       "зооняня: после смены времени снова экран заказа, часы не переспрашиваются")
    await click(CLIENT, "Отмена")


    print("16. Анкета повадок собаки")
    await say(CLIENT, "/profile")
    await click(CLIENT, "Питомцы")
    await click(CLIENT, "Добавить")
    await say(CLIENT, "Бим")
    await click(CLIENT, "Собака")
    await say(CLIENT, "лабродор")
    await click(CLIENT, "Лабрадор")
    r = await say(CLIENT, "средний")
    ok("Особенности" in last_text(r) and "Тянет поводок" in str(_buttons(r[-1])), "после размера собаки — анкета повадок")
    r = await click(CLIENT, "Тянет")
    ok("✅ Тянет поводок" in str(_buttons(r[-1])), "нажатая повадка отмечается галочкой")
    await click(CLIENT, "Агрессия к собакам")
    r = await click(CLIENT, "Тянет")
    ok("✅ Тянет" not in str(_buttons(r[-1])), "повторное нажатие снимает отметку")
    await click(CLIENT, "Подбирает")
    r = await raw_callback(CLIENT, "tr:hack")
    r = await say(CLIENT, "текст вместо кнопок")
    ok("Готово" in last_text(r) or "отметьте" in last_text(r).lower(), "на текст бот напоминает про кнопки и «Готово»")
    r = await click(CLIENT, "Готово")
    r = await say(CLIENT, "Хромает на левую лапу")
    bim = await db.fetch_one("SELECT * FROM pets WHERE name='Бим'")
    ok(bim["traits"] == "pick,aggr_dog", "повадки сохранены кодами в порядке списка: " + str(bim["traits"]))
    ok(bim["notes"] == "Хромает на левую лапу", "здоровье сохранено отдельно в заметках")
    await say(CLIENT, "/profile")
    await click(CLIENT, "Питомцы")
    await click(CLIENT, "Добавить")
    await say(CLIENT, "Тишка")
    await click(CLIENT, "Кошка")
    r = await say(CLIENT, "сиамская")
    if "Сиам" in str(_buttons(r[-1])):
        r = await click(CLIENT, "Сиам")
    r = await click(CLIENT, "S")
    ok("Здоровье" in last_text(r) and "Тянет поводок" not in str(_buttons(r[-1])), "у кошки анкеты повадок нет — сразу здоровье")
    await click(CLIENT, "Ничего")
    await db.execute("UPDATE pets SET is_active=0 WHERE name IN ('Бим','Тишка')")


    print("17. Админу не шлём заявку по частям: ни после телефона, ни после адреса")
    LEAD = 3000
    n0 = len(SENT)
    await say(LEAD, "/start")
    await click(LEAD, "Согласен")
    await click(LEAD, "Заказать")
    await say(LEAD, "Ольга Петрова")
    r = await contact(LEAD, "79161112233")
    ok(not any(m["to"] == ADMIN for m in r), "после телефона админу ничего не приходит")
    r = await say(LEAD, "квартира 46")
    ok("номер квартиры" in last_text(r).lower() and not any(m["to"] == ADMIN for m in r), "«квартира 46» вместо адреса: просит улицу и дом, поиск не запускается")
    r = await say(LEAD, "ул. Ленина, 1")
    ok(len(_buttons(r[-1])) >= 3, "адрес найден: варианты")
    r = await say(LEAD, "кв 46")
    ok("кв. 46" in str(_buttons(r[-1])) and "46" in last_text(r), "номер квартиры после вариантов добавлен к вариантам")
    r = await click(LEAD, "Ленина, 1, Москва")
    ok(not any(m["to"] == ADMIN for m in r), "после адреса админу ничего не приходит")
    await click(LEAD, "Telegram")
    ok((await db.fetch_one("SELECT contact_pref FROM users WHERE tg_id=?", (LEAD,)))["contact_pref"] == "telegram",
       "способ связи «Telegram» сохранён")
    r = await say(LEAD, "Бублик")
    r = await say(LEAD, "кошка")
    ok("Порода" in last_text(r), "вид питомца словом «кошка» понятен без кнопки")
    ok(not any(m["to"] == ADMIN for m in SENT[n0:]), "пока заявка не готова, админу не пришло ни одного сообщения")

    print("18. Политика обработки ПДн")
    POL = 3100
    r = await say(POL, "/start")
    ok("Политика" in str(_buttons(r[0])) or "Политика" in str([_buttons(m) for m in r]), "на экране согласия есть кнопка политики")
    r = await say(POL, "/policy")
    t = "".join(m["text"] for m in r)
    ok("Политика обработки персональных данных" in t and "152-ФЗ" in t, "/policy присылает текст политики даже до согласия")
    r = await click(POL, "Политика")
    ok("Политика обработки персональных данных" in "".join(m["text"] for m in r), "кнопка политики без сайта показывает текст в чате")
    ok(all(len(m["text"]) <= 4096 for m in r), "куски политики помещаются в сообщение Telegram")

    print("19. Удаление данных: «удали меня» и /delete_me")
    import repo as _repo
    GONE = 3000
    await say(GONE, "/start")
    await click(GONE, "Согласен")
    gu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (GONE,))
    await _repo.update_user(gu["id"], name="Глеб", phone="79005556677", address="Мира 3")
    await _repo.add_pet(gu["id"], "Барсик", "кошка", None, None, "боится шума")
    r = await say(GONE, "удали меня")
    ok("Удалить ваши данные" in last_text(r) and "Да, удалить" in str(_buttons(r[-1])), "«удали меня» просит подтверждение, а не удаляет сразу")
    gu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (GONE,))
    ok(gu["name"] == "Глеб", "до подтверждения данные на месте")
    r = await click(GONE, "Отмена")
    ok("ничего не удаляю" in last_text(r) and (await db.fetch_one("SELECT name FROM users WHERE tg_id=?", (GONE,)))["name"] == "Глеб",
       "отмена ничего не стирает")
    await say(GONE, "/delete_me")
    r = await click(GONE, "Да, удалить")
    ok("удалены" in last_text(r), "после подтверждения бот сообщает об удалении")
    gu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (GONE,))
    gp = await db.fetch_one("SELECT * FROM pets WHERE user_id=?", (gu["id"],))
    ok(gu["name"] is None and gu["phone"] is None and gu["address"] is None and gu["pdn_consent"] == 0
       and gp["notes"] is None and gp["name"] != "Барсик", "имя, телефон, адрес, питомец и согласие стёрты")
    r = await say(GONE, "привет")
    ok("Согласен" in str(_buttons(r[-1])), "после удаления бот снова просит согласие")
    n_active = await _repo.active_order_count((await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (CLIENT,)))["id"])
    if n_active:
        r = await say(CLIENT, "удали меня")
        ok("незавершённые заказы" in last_text(r), "с активным заказом данные не удаляются")
        await raw_callback(CLIENT, "del:yes")
        ok((await db.fetch_one("SELECT name FROM users WHERE tg_id=?", (CLIENT,)))["name"] is not None,
           "устаревшая кнопка подтверждения не стирает данные клиента с активным заказом")

    print("20. Ежедневный отчёт в Excel")
    import io as _io

    import openpyxl as _xl
    r = await say(ADMIN, "/report")
    docs = [m for m in r if m.get("document")]
    ok(len(docs) == 1 and docs[0]["document"].endswith(".xlsx") and "новых клиентов" in docs[0]["text"],
       "админ по /report получает файл .xlsx с подписью")
    wb = _xl.load_workbook(_io.BytesIO(docs[0]["data"]))
    ok(wb.sheetnames == ["Новые клиенты", "Заказы"] and wb["Новые клиенты"].max_row >= 2,
       "в файле два листа, сегодняшние клиенты внесены")
    flat = " ".join(str(c.value) for ws in wb for row in ws.iter_rows() for c in row)
    ok("Глеб" not in flat and "79005556677" not in flat, "клиент, удалённый по просьбе, в отчёт не попадает")
    r = await say(ADMIN, "/report вчера")
    ok("ГГГГ-ММ-ДД" in last_text(r), "кривая дата — подсказка формата")
    r = await say(CLIENT, "/report")
    ok(not any(m.get("document") for m in r), "клиенту отчёт не отправляется")
    from bot import setup_scheduler
    ok(setup_scheduler(bot).get_job("daily_report") is not None, "отчёт стоит в расписании на конец дня")

    # ------------------------------------------------------------------
    print("21. Кнопки администратора: подтвердить, ссылка, оплата, исполнитель")
    import orders as _orders
    import repo as _repo2
    import texts as _texts
    from utils import normalize_phone as _np
    os.environ["PAY_SBP_PHONE"] = "+79990001122"
    os.environ["PAY_CARD"] = "2200 0000 0000 0000"
    os.environ["PAY_RECIPIENT"] = "Иван И."
    await db.execute("UPDATE orders SET status='cancelled' WHERE status IN ('created','confirmed')")  # чистый лист
    NEW = 4000
    await say(NEW, "/start")
    await click(NEW, "Согласен")
    nu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (NEW,))
    await _repo2.update_user(nu["id"], name="Анна", phone=_np("79007778899"), address="ул. Ленина, 1, Москва",
                             address_verified=1)
    npet = await _repo2.add_pet(nu["id"], "Дымок", "собака", "Лабрадор-ретривер", "L", None)
    await say(NEW, "/menu")
    await click(NEW, "Заказать")
    await svc(NEW, "Выгул 30")
    await say(NEW, future.replace(future[-5:], "10:00"))
    r = await click(NEW, "Комментарий")
    ok("комментарий" in last_text(r).lower(), "на экране заказа есть кнопка комментария")
    r = await say(NEW, "домофон 12, позвоните за 10 минут")
    ok("Комментарий: домофон 12" in last_text(r), "комментарий виден на экране заказа")
    r = await click(NEW, "Подтвердить")
    adm = [m for m in r if m["to"] == ADMIN and "Новая заявка" in m["text"]]
    ok(adm and "/confirm" not in adm[0]["text"] and "домофон 12" in adm[0]["text"], "заявка админу: без команд, с комментарием")
    labels = [b.text for b in _buttons(adm[0])]
    ok(any("Подтвердить" in t for t in labels) and any("Отклонить" in t for t in labels)
       and any(b.url == "https://t.me/u4000" for b in _buttons(adm[0])), "у заявки кнопки «Подтвердить», «Отклонить» и чат с клиентом")
    oid1 = (await db.fetch_one("SELECT MAX(id) m FROM orders"))["m"]
    r = await click(ADMIN, "Подтвердить")
    ok((await db.fetch_one("SELECT status FROM orders WHERE id=?", (oid1,)))["status"] == "confirmed", "кнопка подтвердила заказ")
    ok(any(m["to"] == NEW and "оплатить" in m["text"].lower() and "СБП" in str(_buttons(m)) and "карт" in str(_buttons(m))
           for m in r), "клиент сразу получил дежурные способы оплаты (СБП и карта)")
    ok(any(m["to"] == ADMIN and "ссылку" in m["text"] for m in r), "админу подсказано просто прислать ссылку")
    r = await say(ADMIN, "https://pay.example/777")
    ok("№%d" % oid1 in last_text(r, ADMIN) and "отправлена" in last_text(r, ADMIN), "ссылка принята: понятно, по какому заказу и что отправлена")
    ok((await db.fetch_one("SELECT payment_link FROM payments WHERE order_id=?", (oid1,)))["payment_link"] == "https://pay.example/777",
       "ссылка записана в платёж этого заказа")
    ok(any(m["to"] == NEW and any(b.url == "https://pay.example/777" for b in _buttons(m)) for m in r), "клиент получил кнопку со ссылкой")
    r = await click(NEW, "СБП")
    ok("+79990001122" in last_text(r) and f"заказ №{oid1}" in last_text(r), "по кнопке СБП клиент видит номер и номер заказа")
    r = await click(NEW, "карту")
    ok("2200 0000 0000 0000" in last_text(r), "по кнопке карты клиент видит карту")
    r = await click(NEW, "Я оплатил")
    ok("Проверяем" in last_text(r, NEW) and any(m["to"] == ADMIN and "оплатил" in m["text"] for m in r), "«Я оплатил» уходит админу на проверку")
    r = await click(ADMIN, "Оплата пришла")
    ok((await db.fetch_one("SELECT status FROM orders WHERE id=?", (oid1,)))["status"] == "paid", "админ подтвердил оплату кнопкой")
    ok("исполнителя" in last_text(r, ADMIN).lower() and "Мария" in str(_buttons(r[-1])), "после оплаты сразу предложены исполнители")
    r = await click(ADMIN, "Мария")
    ok((await db.fetch_one("SELECT status FROM orders WHERE id=?", (oid1,)))["status"] == "assigned", "исполнитель назначен кнопкой")

    async def mk_order(uid_db, pet_id, status="created"):
        oid = await _orders.create_order(
            {"user_id": uid_db, "pet_id": pet_id, "service_id": 1, "start_at": __import__("utils").now_utc() + timedelta(days=4),
             "end_at": None, "quantity": 1, "address": "ул. Ленина, 1", "address_to": None, "is_urgent": False,
             "outside_mkad": False}, 800, [], actor="test")
        if status != "created":
            await db.execute("UPDATE orders SET status=? WHERE id=?", (status, oid))
        return oid
    o2, o3 = await mk_order(nu["id"], npet), await mk_order(nu["id"], npet)
    await say(ADMIN, f"/confirm {o2}")
    await say(ADMIN, f"/confirm {o3}")
    r = await say(ADMIN, "https://pay.example/888")
    ok("К какому заказу" in last_text(r) and f"№{o2}" in str(_buttons(r[-1])) and f"№{o3}" in str(_buttons(r[-1])),
       "ждущих оплаты заказов несколько: бот спрашивает, к какому ссылка")
    r = await click(ADMIN, f"№{o3}")
    ok((await db.fetch_one("SELECT payment_link FROM payments WHERE order_id=?", (o3,)))["payment_link"] == "https://pay.example/888"
       and (await db.fetch_one("SELECT payment_link FROM payments WHERE order_id=?", (o2,)))["payment_link"] is None,
       "ссылка ушла в выбранный заказ, второй не тронут")
    await say(ADMIN, f"/cancel {o2}")
    r = await say(NEW, "оплатил")
    ok("Проверяем" in last_text(r, NEW) and any(m["to"] == ADMIN and f"№{o3}" in m["text"] for m in r),
       "клиент просто пишет «оплатил» — админ получает проверку по нужному заказу")
    r = await click(ADMIN, "Не вижу")
    ok(any(m["to"] == NEW and "Пока не вижу" in m["text"] for m in r), "«Не вижу» — клиенту уходит просьба проверить")
    r = await say(ADMIN, "https://pay.example/999")
    ok("К какому" not in last_text(r) and f"№{o3}" in last_text(r), "ждущий оплаты заказ один — ссылка уходит сразу")
    await say(ADMIN, f"/cancel {o3}")
    r = await say(ADMIN, "https://pay.example/000")
    ok("Нет подтверждённых" in last_text(r), "ссылка без ждущих оплаты заказов: понятное объяснение")
    o4 = await mk_order(nu["id"], npet)
    r = await raw_callback(ADMIN, f"ap:no:{o4}")
    ok((await db.fetch_one("SELECT status FROM orders WHERE id=?", (o4,)))["status"] == "cancelled"
       and any(m["to"] == NEW and "отмен" in m["text"].lower() for m in r), "«Отклонить» отменяет заявку и сообщает клиенту")
    o4 = await mk_order(nu["id"], npet)

    print("22. Меню администратора: только управление ботом")
    r = await say(ADMIN, "/menu")
    ok("Меню администратора" in last_text(r) and "Заказать" not in str(_buttons(r[-1])), "в меню админа нет кнопки заказа")
    r = await say(ADMIN, "/start")
    ok("Меню администратора" in last_text(r) and "Согласен" not in str(_buttons(r[-1])), "админу не нужно согласие клиента")
    r = await raw_callback(ADMIN, "menu:order")
    ok("Меню администратора" in last_text(r) and "Выберите услугу" not in last_text(r), "заказать с админского аккаунта нельзя")
    r = await raw_callback(ADMIN, "svc:1")
    ok("Выберите" not in last_text(r) and "Меню администратора" in last_text(r), "старая кнопка услуги у админа не запускает заказ")
    for label in ("Активные заказы", "Услуги и цены", "Исполнители", "Способы оплаты", "Все команды"):
        await say(ADMIN, "/menu")
        r = await click(ADMIN, label)
        ok(r and last_text(r), f"кнопка админ-меню «{label}» отвечает")
    r = await say(ADMIN, "/menu")
    r = await click(ADMIN, "Способы оплаты")
    ok("СБП" in last_text(r) and "Railway" in last_text(r) and "2200" not in last_text(r), "способы оплаты: настроены, реквизиты в чат не выводятся")
    r = await say(ADMIN, "/menu")
    r = await click(ADMIN, "Наценки")
    ok("Наценки" in last_text(r) and "➕" in str(_buttons(r[-1])), "наценки — панель с кнопками ➖ ➕ 🗑")
    rule = await db.fetch_one("SELECT * FROM price_rules WHERE active=1 ORDER BY id LIMIT 1")
    r = await raw_callback(ADMIN, f"rl:up:{rule['id']}")
    after = await db.fetch_one("SELECT * FROM price_rules WHERE id=?", (rule["id"],))
    step = 50 if rule["effect_type"] == "fixed" else 10
    ok(after["value"] == rule["value"] + step, "➕ поднимает наценку без команд")
    await raw_callback(ADMIN, f"rl:dn:{rule['id']}")
    ok((await db.fetch_one("SELECT value FROM price_rules WHERE id=?", (rule["id"],)))["value"] == rule["value"], "➖ возвращает назад")
    await raw_callback(CLIENT, f"rl:off:{rule['id']}")
    await raw_callback(CLIENT, f"ap:ok:{o4}")
    ok((await db.fetch_one("SELECT active FROM price_rules WHERE id=?", (rule["id"],)))["active"] == 1
       and (await db.fetch_one("SELECT status FROM orders WHERE id=?", (o4,)))["status"] == "created",
       "обычному клиенту админские кнопки не действуют")

    print("23. Услуги: сначала четыре, внутри варианты; больше часов; комментарий")
    await say(CLIENT, "/menu")
    r = await click(CLIENT, "Заказать")
    names = [b.text for b in _buttons(r[-1])]
    ok(len([n for n in names if "—" in n]) == 4 and any("Выгул" in n for n in names) and any("Зооняня" in n for n in names)
       and any("Передержка" in n for n in names) and any("Зоотакси" in n for n in names), "в меню услуг ровно четыре услуги")
    r = await click(CLIENT, "Выгул")
    ok(any("30 минут" in b.text for b in _buttons(r[-1])) and any("60 минут" in b.text for b in _buttons(r[-1])), "внутри выгула — варианты по времени")
    r = await click(CLIENT, "Назад")
    ok("Выберите услугу" in last_text(r), "из вариантов можно вернуться к четырём услугам")
    r = await click(CLIENT, "Зооняня")
    ok("Приходящая" in str(_buttons(r[-1])) and "проживанием" in str(_buttons(r[-1])), "внутри зоонянь — приходящая или с проживанием")
    await click(CLIENT, "Приходящая")
    r = await say(CLIENT, future.replace(future[-5:], "12:00"))
    btn = [b.text for b in _buttons(r[-1])]
    ok("12" in btn and "10" in btn and any("Другое" in t for t in btn), "для часов есть кнопки до 12 и «Другое»")
    r = await click(CLIENT, "Другое")
    ok("число" in last_text(r).lower() and "комментар" in last_text(r).lower(), "«Другое» просит число и подсказывает про комментарий")
    r = await say(CLIENT, "20")
    ok("Проверьте заказ" in last_text(r) and "20" in last_text(r), "можно выбрать 20 часов")
    await click(CLIENT, "Отмена")
    await click(CLIENT, "Заказать")
    await click(CLIENT, "Зооняня")
    await click(CLIENT, "Приходящая")
    await say(CLIENT, future.replace(future[-5:], "12:00"))
    r = await say(CLIENT, "30")
    ok("от 1 до 24" in last_text(r), "больше 24 часов — понятное объяснение, а не молчание")
    await click(CLIENT, "Отмена")

    print("24. «Мои заказы» и подсказка о последнем заказе")
    r = await say(NEW, "/menu")
    ok("Последний заказ:" in last_text(r) and "Выгул" in last_text(r), "в меню есть подсказка о последнем заказе")
    ok(any("Повторить" in b.text and "Выгул" in b.text for b in _buttons(r[-1])), "кнопка повтора называет услугу")
    r = await click(NEW, "Мои заказы")
    t = last_text(r)
    ok("🟢 В работе" in t and "Завершённые и отменённые" in t and t.index("🟢 В работе") < t.index("Завершённые и отменённые"),
       "активные заказы сверху и подсвечены, отменённые отдельным блоком ниже")
    ok(t.count("🟢") >= 2 and "▫️" in t, "активные с 🟢, остальные с ▫️")
    await db.execute("UPDATE orders SET status='in_progress' WHERE id=?", (oid1,))
    r = await click(NEW, "Мои заказы")
    ok(f"🟢 №{oid1}" in last_text(r) and "В работе" in last_text(r), "заказ в работе остаётся подсвеченным")

    print("25. Удаление данных не тупик + узнавание по телефону")
    ERA = 4100
    await say(ERA, "/start")
    await click(ERA, "Согласен")
    eu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (ERA,))
    await _repo2.update_user(eu["id"], name="Эра", phone=_np("79001110000"), address="Мира 3")
    epet = await _repo2.add_pet(eu["id"], "Зевс", "собака", None, "M", None)
    eo = await mk_order(eu["id"], epet)
    r = await say(ERA, "удали меня")
    ok("Отменить заказ" in str(_buttons(r[-1])), "удалить нельзя, но есть кнопки: отменить неоплаченный заказ")
    r = await click(ERA, "Отменить заказ")
    r = await click(ERA, "Да, отменить")
    ok((await db.fetch_one("SELECT status FROM orders WHERE id=?", (eo,)))["status"] == "cancelled"
       and any(m["to"] == ERA for m in r), "заказ отменён, клиент получил сообщение")
    ok(any(m["to"] == ADMIN for m in r), "администратор тоже узнал об отмене клиентом")
    r = await say(ERA, "удали меня")
    ok("Да, удалить" in str(_buttons(r[-1])), "после отмены удаление данных доступно")
    await click(ERA, "Да, удалить")
    r = await say(ERA, "/start")
    r = await click(ERA, "Согласен")
    ok("Последний заказ" not in last_text(r), "после удаления прошлый заказ в меню не подсказывается")
    r = await raw_callback(ERA, "menu:orders")
    ok("пока нет" in last_text(r).lower(), "после удаления «Мои заказы» пусты")
    # оплаченный заказ: кнопка «попросить администратора»
    PAID = 4150
    await say(PAID, "/start")
    await click(PAID, "Согласен")
    pu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (PAID,))
    await _repo2.update_user(pu["id"], name="Паша", phone=_np("79002220000"), address="Мира 4")
    ppet = await _repo2.add_pet(pu["id"], "Тор", "собака", None, "M", None)
    await mk_order(pu["id"], ppet, status="paid")
    r = await say(PAID, "удали меня")
    ok("администратора" in str(_buttons(r[-1])) and "Отменить заказ" not in str(_buttons(r[-1])), "оплаченный заказ: можно позвать администратора")
    r = await click(PAID, "администратора")
    ok(any(m["to"] == ADMIN and "просит удалить" in m["text"] for m in r) and "Передал" in last_text(r, PAID), "администратор получил просьбу")

    # узнавание: тот же номер, другой Telegram-аккаунт
    REC = 4200
    await say(REC, "/start")
    await click(REC, "Согласен")
    await click(REC, "Заказать")
    await say(REC, "Анна")
    r = await contact(REC, "79007778899")
    ok("уже есть у нас" in last_text(r) and "Пропустить" in str(_buttons(r[-1])), "номер уже в базе: бот просит второй признак")
    r = await say(REC, "Шарик")
    ok("Не совпало" in last_text(r), "неверный второй признак не открывает чужие данные")
    ok((await db.fetch_one("SELECT address FROM users WHERE tg_id=?", (REC,)))["address"] is None, "до совпадения адрес не подтянут")
    r = await say(REC, "Дымок")
    ok("Узнал вас" in last_text(r, REC) or any("Узнал вас" in m["text"] for m in r), "кличка совпала — профиль узнан")
    ru = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (REC,))
    ok(ru["address"] == "ул. Ленина, 1, Москва" and ru["name"] == "Анна", "адрес и имя перенесены")
    ok(len(await db.fetch_all("SELECT * FROM pets WHERE user_id=? AND is_active=1", (ru["id"],))) == 1, "питомец перенесён")
    ok(_texts.CHOOSE_SERVICE in last_text(r), "после узнавания сразу к выбору услуги")
    # по адресу
    REC2 = 4210
    await say(REC2, "/start")
    await click(REC2, "Согласен")
    await click(REC2, "Заказать")
    await say(REC2, "Анна")
    await contact(REC2, "79007778899")
    r = await say(REC2, "Ленина 1")
    ok(any("Узнал вас" in m["text"] for m in r), "улица и дом тоже подходят вторым признаком")
    # пропуск
    REC3 = 4220
    await say(REC3, "/start")
    await click(REC3, "Согласен")
    await click(REC3, "Заказать")
    await say(REC3, "Анна")
    await contact(REC3, "79007778899")
    r = await click(REC3, "Пропустить")
    ok("адрес" in last_text(r).lower() and (await db.fetch_one("SELECT address FROM users WHERE tg_id=?", (REC3,)))["address"] is None,
       "«Пропустить» — обычный опрос, чужие данные не подтянуты")
    # две ошибки подряд
    REC4 = 4230
    await say(REC4, "/start")
    await click(REC4, "Согласен")
    await click(REC4, "Заказать")
    await say(REC4, "Анна")
    await contact(REC4, "79007778899")
    await say(REC4, "Шарик")
    r = await say(REC4, "Бобик")
    ok("адрес" in last_text(r).lower(), "после двух промахов — обычный опрос")
    # напечатанный номер не даёт узнавания
    REC5 = 4240
    await say(REC5, "/start")
    await click(REC5, "Согласен")
    await click(REC5, "Заказать")
    await say(REC5, "Анна")
    r = await say(REC5, "+7 900 777-88-99")
    ok("кнопкой" in last_text(r).lower() and "уже есть у нас" not in last_text(r),
       "напечатанный номер в Telegram не принимается и профиль не раскрывает")
    # профиль: только главное, остальное в подменю
    r = await say(NEW, "/profile")
    btns = [b.text for b in _buttons(r[-1])]
    ok("Связь:" in last_text(r), "в профиле виден способ связи")
    ok(len(btns) == 4 and any("Питомцы" in b for b in btns) and any("Настройки" in b for b in btns),
       "в профиле 4 кнопки: данные, питомцы, настройки, меню: " + str(btns))
    ok(not any("Удалить" in b or "акции" in b.lower() or "Способ связи" in b for b in btns), "удаление, рассылки и способ связи убраны из профиля")
    r = await click(NEW, "Настройки")
    ok("Уведомления о заказах: включены" in last_text(r), "в настройках видны уведомления")
    sb = [b.text for b in _buttons(r[-1])]
    ok(any("Способ связи" in b for b in sb) and any("Удалить" in b for b in sb) and any("акции" in b.lower() for b in sb),
       "в настройках: способ связи, рассылки, удаление данных")
    r = await click(NEW, "Способ связи")
    r = await click(NEW, "СМС")
    ok((await db.fetch_one("SELECT contact_pref FROM users WHERE tg_id=?", (NEW,)))["contact_pref"] == "sms"
       and "СМС" in last_text(r), "способ связи меняется из настроек")
    r = await say(NEW, "/profile")
    r = await click(NEW, "Настройки")
    r = await click(NEW, "Удалить")
    ok("Да, удалить" in str(_buttons(r[-1])) or "заказ" in last_text(r).lower() or "администратора" in str(_buttons(r[-1])),
       "удаление данных работает из настроек")
    await click(NEW, "Нет") if "Нет" in str(_buttons(r[-1])) else None

    print("26. Питомцы в профиле: список, изменить, убрать, добавить")
    PET = 4300
    await say(PET, "/start")
    await click(PET, "Согласен")
    pu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (PET,))
    await _repo2.update_user(pu["id"], name="Петя", phone=_np("79003330000"), address="Мира 5")
    await _repo2.add_pet(pu["id"], "Рекс", "собака", None, "M", None)
    await _repo2.add_pet(pu["id"], "Мурка", "кошка", None, "S", None)
    r = await say(PET, "/profile")
    r = await click(PET, "Питомцы")
    ok("Рекс" in str(_buttons(r[-1])) and "Мурка" in str(_buttons(r[-1])) and "Добавить" in str(_buttons(r[-1])),
       "список питомцев: каждый кнопкой + добавить")
    r = await click(PET, "Рекс")
    ok("Рекс" in last_text(r) and "Изменить" in str(_buttons(r[-1])) and "Убрать" in str(_buttons(r[-1])), "карточка питомца: изменить и убрать")
    r = await click(PET, "Изменить")
    ok("кличка" in last_text(r).lower() or "зовут" in last_text(r).lower(), "изменение начинается с клички")
    await say(PET, "Рексик")
    await click(PET, "Собака")
    r = await say(PET, "лабрадор")
    ok("размер" in last_text(r).lower() or "Размер" in str(_buttons(r[-1])) or "S" in str(_buttons(r[-1])), "дальше размер")
    r = await click(PET, "L")
    if "Тянет" in str(_buttons(r[-1])) or "tr:" in str([b.callback_data for b in _buttons(r[-1])]):
        r = await click(PET, "Готово")
    r = await click(PET, "Ничего")
    ok(any("Заказать услугу" in b.text for b in _buttons(next(m for m in reversed(SENT) if m["to"] == PET and "Сохранил" in m["text"]))),
       "после сохранения бот сам предлагает заказать услугу")
    pets = await db.fetch_all("SELECT name, size, is_active FROM pets WHERE user_id=? AND is_active=1 ORDER BY id", (pu["id"],))
    ok([p["name"] for p in pets] == ["Рексик", "Мурка"] and pets[0]["size"] == "L", "питомец обновлён на месте, второй не задет, дубля нет")
    r = await say(PET, "/profile")
    r = await click(PET, "Питомцы")
    r = await click(PET, "Мурка")
    r = await click(PET, "Убрать")
    ok("Да" in str(_buttons(r[-1])), "убрать питомца — с подтверждением")
    r = await click(PET, "Да")
    ok([p["name"] for p in await db.fetch_all("SELECT name FROM pets WHERE user_id=? AND is_active=1", (pu["id"],))] == ["Рексик"],
       "питомец убран из списка (в заказах остаётся)")
    # чужого питомца изменить/убрать нельзя
    other = await db.fetch_one("SELECT p.id FROM pets p JOIN users u ON u.id=p.user_id WHERE u.tg_id<>? AND p.is_active=1 LIMIT 1", (PET,))
    if other:
        await raw_callback(PET, f"pet:delyes:{other['id']}")
        ok((await db.fetch_one("SELECT is_active FROM pets WHERE id=?", (other["id"],)))["is_active"] == 1, "чужого питомца убрать нельзя")


    print("27. Зоотакси: какой питомец, обязательное предупреждение, для кого заказ")
    TX = 4400
    await say(TX, "/start")
    await click(TX, "Согласен")
    tu = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (TX,))
    await _repo2.update_user(tu["id"], name="Таня", phone=_np("79004440000"), address="ул. Ленина, 1, Москва", address_verified=1)
    await _repo2.add_pet(tu["id"], "Рекс", "собака", None, "M", None)
    await _repo2.add_pet(tu["id"], "Пеперони", "кошка", None, "S", None)
    await say(TX, "/menu")
    await click(TX, "Заказать")
    r = await svc(TX, "Зоотакси")
    bt = str(_buttons(r[-1]))
    ok("поедет" in last_text(r).lower() and "Рекс" in bt and "Пеперони" in bt and "Другой питомец" in bt,
       "такси: бот спрашивает, кто поедет — питомцы из базы и «Другой питомец»")
    r = await click(TX, "Рекс")
    ok("намордник" in last_text(r).lower() and "подстилк" in last_text(r).lower() and "Понял" in str(_buttons(r[-1])),
       "собака: обязательное предупреждение про намордник и подстилку")
    r = await say(TX, "завтра в 10")
    ok("Понял" in last_text(r), "пока не нажато «Понял», дальше не пускает")
    r = await click(TX, "Понял")
    ok("Для кого" in last_text(r) and "Для меня" in str(_buttons(r[-1])) and "другого" in str(_buttons(r[-1])), "дальше: для кого заказ")
    r = await click(TX, "другого")
    ok("зовут" in last_text(r).lower(), "для другого человека: просит имя")
    r = await say(TX, "Борис")
    r = await say(TX, "123")
    ok("номер" in last_text(r).lower(), "неверный телефон пассажира не принимается")
    r = await say(TX, "8 916 555-44-33")
    ok(_texts.ASK_WHEN_TAXI[:20] in last_text(r) or "машину" in last_text(r), "после имени и телефона — выбор времени")
    await say(TX, future.replace(future[-5:], "13:00"))
    await click(TX, "📍")
    await say(TX, "Москва, Шереметьево, терминал B")
    await click(TX, "Шереметьево")
    r = await click(TX, "Подтвердить")
    ro = await db.fetch_one("SELECT rider_name, rider_phone FROM orders WHERE user_id=? ORDER BY id DESC", (tu["id"],))
    ok(ro["rider_name"] == "Борис" and ro["rider_phone"] == "+79165554433", "имя и телефон пассажира сохранены в заказе")
    ok(any(m["to"] == ADMIN and "Борис" in m["text"] and "+79165554433" in m["text"] for m in r), "админу в заявке видно, кто поедет")
    # для себя и кошка: в переноске
    await say(TX, "/menu")
    await click(TX, "Заказать")
    await svc(TX, "Зоотакси")
    r = await click(TX, "Пеперони")
    ok("переноск" in last_text(r).lower() and "намордник" not in last_text(r).lower(), "кошка: предупреждение про переноску, без намордника")
    await click(TX, "Понял")
    await click(TX, "Для меня")
    await say(TX, future.replace(future[-5:], "14:00"))
    await click(TX, "📍")
    await say(TX, "Москва, Шереметьево, терминал B")
    await click(TX, "Шереметьево")
    await click(TX, "Подтвердить")
    ro = await db.fetch_one("SELECT rider_name, pet_id FROM orders WHERE user_id=? ORDER BY id DESC", (tu["id"],))
    ok(ro["rider_name"] is None, "для себя: пассажир не указывается")
    cat = await db.fetch_one("SELECT id FROM pets WHERE name='Пеперони'")
    ok(ro["pet_id"] == cat["id"], "в заказ попал выбранный питомец, а не первый в списке")
    # другой питомец: сначала вносим
    await say(TX, "/menu")
    await click(TX, "Заказать")
    await svc(TX, "Зоотакси")
    r = await click(TX, "Другой питомец")
    ok("зовут" in last_text(r).lower() or "кличка" in last_text(r).lower(), "«Другой питомец»: сначала вносим его в базу")
    await say(TX, "Кеша")
    await click(TX, "Другое")
    await say(TX, "попугай")
    await click(TX, "S")
    await click(TX, "Ничего")
    ok(await db.fetch_one("SELECT 1 FROM pets WHERE user_id=? AND name='Кеша' AND is_active=1", (tu["id"],)) is not None, "новый питомец добавлен в профиль")
    ok("переноск" in last_text(SENT[-3:], TX).lower() or any("переноск" in m["text"] for m in SENT[-3:] if m["to"] == TX),
       "после добавления питомца сразу предупреждение, без возврата в меню")

    await say(TX, "/menu")
    r = await raw_callback(TX, "tw:ok")
    ok("Для кого" not in last_text(r), "старая кнопка «Понял» вне заказа такси ничего не запускает")
    r = await raw_callback(TX, "tf:other")
    ok("зовут" not in last_text(r).lower(), "старая кнопка «Для другого» вне заказа ничего не запускает")

    print("28. Переписка и портал: вопрос клиента — админу, регистрация подтверждается, вход в портал из бота")
    await say(TX, "/menu")
    r = await say(TX, "А можно с двумя собаками?")
    ok(any(m["to"] == ADMIN and "А можно с двумя собаками?" in m["text"] for m in r), "вопрос клиента вне сценария пришёл админу")
    ok(any("wc:" in (b.callback_data or "") for m in r if m["to"] == ADMIN for b in _buttons(m)), "у админа кнопка «Ответить»")
    ok("Передал администратору" in last_text(r, TX) and "Заказать услугу" in str(_buttons(r[-1])), "клиенту: передано + предложение заказать")
    ok((await db.fetch_one("SELECT party_kind, direction FROM messages WHERE party_tg_id=? ORDER BY id DESC", (TX,)))["direction"] == "in",
       "вопрос сохранён для портала")
    await click(ADMIN, "Ответить")
    r = await say(ADMIN, "Да, можно")
    ok(any(m["to"] == TX and "Да, можно" in m["text"] for m in r), "ответ админа дошёл клиенту")
    await click(ADMIN, "Закончить чат")
    r = await say(ADMIN, "просто текст админа")
    ok(not any(m["to"] == ADMIN and "✉️" in m["text"] for m in r), "свой текст админа самому себе не пересылается")
    r = await say(ADMIN, "/start webadmin")
    ok(any("/chat/login?t=" in m["text"] and "n=portal" in m["text"] for m in r), "«Войти через Telegram» на сайте: бот присылает ссылку в портал")
    r = await say(ADMIN, "/portal")
    ok("Портал администратора" in last_text(r), "/portal — ссылка на портал")
    REG = 4500
    await say(REG, "/start")
    await click(REG, "Согласен")
    await click(REG, "Заказать")
    await say(REG, "Рита")
    await contact(REG, "79005550000")
    await say(REG, "ул. Ленина, 1")
    await click(REG, "Ленина, 1, Москва")
    await click(REG, "Telegram")
    await say(REG, "Барон")
    await click(REG, "Собака")
    await say(REG, "лабрадор")
    await click(REG, "M")
    await click(REG, "Готово")
    r = await click(REG, "Ничего")
    ok(any(m["to"] == REG and "Регистрация подтверждена через Telegram" in m["text"] for m in r),
       "после регистрации клиент получает подтверждение в Telegram")

    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


asyncio.run(main())
