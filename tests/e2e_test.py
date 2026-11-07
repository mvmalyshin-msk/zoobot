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
from aiogram.methods import AnswerCallbackQuery, SendMessage  # noqa: E402
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


geo.search, geo.reverse = fake_search, fake_reverse
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
    ok("не нашла" in last_text(r).lower() and "Оставить как написано" not in str(_buttons(r[-1])),
       "несуществующий адрес: просит переписать, «оставить как есть» ещё нет")
    r = await say(CLIENT, "абракадабра 9")
    ok("Оставить как написано" in str(_buttons(r[-1])), "после второй неудачи появляется «оставить как написано»")
    r = await say(CLIENT, "ул. Ленина, 1, кв. 5")
    ok(len(_buttons(r[-1])) >= 3 and "Ленина" in str(_buttons(r[-1])), "адрес найден: варианты кнопками")
    r = await click(CLIENT, "Ленина, 1, Москва")
    ok("Как его зовут" in last_text(r), "после выбора адреса — вопрос о питомце")
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
    ok("Особенности" in last_text(r), "слово «большой» понято как размер L")
    r = await click(CLIENT, "Ничего")
    pet = await db.fetch_one("SELECT * FROM pets")
    ok(pet["name"] == "Рекс" and pet["breed"] == "Лабрадор-ретривер" and pet["size"] == "L", "питомец сохранён с проверенной породой")
    ok("Выберите услугу" in last_text(r), "после профиля сразу выбор услуги")
    u = await db.fetch_one("SELECT * FROM users")
    ok(u["phone"] == "+79001234567" and u["address"].startswith("ул. Ленина"), "профиль сохранён")

    print("2б. Ещё питомцы: другое животное, чужая порода, редкая порода")
    await say(CLIENT, "/profile")
    await click(CLIENT, "питомц")
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
    await click(CLIENT, "питомц")
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
    await click(CLIENT, "Выгул 60")
    r = await say(CLIENT, "завтра 22:30")
    txt = last_text(r)
    # (700 + 100 за крупную собаку) × 1.3 ночью = 1040
    ok("1 040 ₽" in txt and "Крупная собака" in txt and "Ночное" in txt, "разбивка: база + L + ночь = 1 040 ₽")
    r = await click(CLIENT, "За МКАД")
    ok("(700 + 100 + 150) × 1.3" or "1 235 ₽" in last_text(r), "за МКАД пересчитывает")
    ok("1 235 ₽" in last_text(r), "итог за МКАД = 1 235 ₽")
    await click(CLIENT, "За МКАД")
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
    ok(any(m["to"] == CLIENT and "завтра в 22:30" in m["text"] for m in SENT), "клиент получил напоминание за 24 ч")

    print("5. Исполнитель ведёт заказ, клиент оставляет отзыв")
    await click(EXEC, "Начал")
    o = await db.fetch_one("SELECT status FROM orders WHERE id=1")
    ok(o["status"] == "in_progress", "assigned → in_progress (кнопкой исполнителя)")
    r = await click(EXEC, "Завершил")
    ok(any(m["to"] == CLIENT and "Оцените" in m["text"] for m in r), "completed → клиенту запрос оценки")
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
    r = await say(CLIENT, "завтра 10:00")
    ok("Проверьте заказ" in last_text(r) and "Рекс" in last_text(r), "повтор: спросил только время, остальное из профиля")
    await click(CLIENT, "Отмена")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await click(CLIENT, "Зооняня приходящая")
    await say(CLIENT, "через неделю")
    r = await say(CLIENT, "01.01.2020 10:00")
    ok("будущем" in last_text(r), "дата в прошлом отклоняется")
    r = await say(CLIENT, "01.01.2099 10:00")
    ok("далеко" in last_text(r), "дата через десятилетия (опечатка в годе) отклоняется")
    future = (datetime.now() + timedelta(days=3)).strftime("%d.%m %H:%M")
    r = await say(CLIENT, future.replace(future[-5:], "11:00"))
    r = await click(CLIENT, "3")
    ok("3 ч × 600 ₽" in last_text(r) and "1 900 ₽" in last_text(r), "зооняня 3 ч: 1 800 + 100 за крупную = 1 900 ₽")
    await click(CLIENT, "Отмена")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await click(CLIENT, "Зоотакси")
    await say(CLIENT, future.replace(future[-5:], "12:00"))
    await click(CLIENT, "📍")
    r = await say(CLIENT, "Москва, Шереметьево, терминал B")
    ok("Москва, Шереметьево, терминал B" in str(_buttons(r[-1])), "точка Б: тоже варианты с карты")
    r = await click(CLIENT, "Шереметьево")
    t = last_text(r)
    ok("Откуда: ул. Ленина" in t and "Куда: Москва" in t, "такси: точки А и Б на экране подтверждения")
    ok("За МКАД: да" in str(_buttons(r[-1])), "такси в аэропорт: «за МКАД» определено по карте само")
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
    await click(CLIENT, "Выгул 30")
    await say(CLIENT, future.replace(future[-5:], "15:00"))
    global MAP_DOWN
    MAP_DOWN = True
    await click(CLIENT, "Изменить адрес")
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
    await click(CLIENT, "Получать рассылки")
    r = await say(ADMIN, "/broadcast Скидка 10% на выгул")
    ok("доставлено 1" in last_text(r), "после согласия рассылка доходит")
    ok(any(m["to"] == CLIENT and "Скидка 10%" in m["text"] for m in SENT), "клиент получил рассылку")
    log_n = (await db.fetch_one("SELECT COUNT(*) n FROM admin_log"))["n"]
    ok(log_n >= 15, f"действия админа в admin_log ({log_n} записей)")
    r = await say(CLIENT, "/orders")
    ok("Что нужно вашему питомцу" in last_text(r), "обычному клиенту админ-команды недоступны")

    bad = await db.fetch_all("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN "
                             "('schema_version','holidays','sqlite_sequence') AND name NOT LIKE 'sqlite_%'")
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
    await click(CLIENT, "Выгул 30")
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
    await click(CLIENT, "Выгул 30")
    await say(CLIENT, future.replace(future[-5:], "17:00"))
    before = (await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"]
    await asyncio.gather(click(CLIENT, "Подтвердить"), click(CLIENT, "Подтвердить"))
    after = (await db.fetch_one("SELECT COUNT(*) n FROM orders"))["n"]
    ok(after == before + 1, f"двойной тап: создан один заказ (было {before}, стало {after})")

    print("12. Адрес из мини-приложения с картой")
    await say(CLIENT, "/menu")
    await click(CLIENT, "Заказать")
    await click(CLIENT, "Выгул 30")
    r = await say(CLIENT, future.replace(future[-5:], "18:00"))
    await click(CLIENT, "Изменить адрес")
    r = await web_app_pick(CLIENT, {"lat": 59.9, "lon": 30.3, "label": "Невский проспект, 1", "flat": ""})
    ok("не получилось" in last_text(r).lower(), "точка вне Москвы и области отклоняется")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "ывфвф 5", "flat": ""})
    ok("не получилось" in last_text(r).lower(), "мусорный текст адреса из карты отклоняется")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "Зеленоград, корпус 1105", "flat": "12"})
    t = last_text(r)
    ok("Адрес: Зеленоград, корпус 1105, кв. 12" in t, "адрес с карты и квартира попали на экран заказа")
    ok("За МКАД: да" in str(_buttons(r[-1])), "«за МКАД» определено по координатам точки")
    r = await click(CLIENT, "Подтвердить")
    o = await db.fetch_one("SELECT * FROM orders ORDER BY id DESC LIMIT 1")
    ok(o["address_unverified"] == 0 and o["outside_mkad"] == 1, "заказ: адрес подтверждён картой, за МКАД")
    r = await web_app_pick(CLIENT, {"lat": 55.97, "lon": 37.2, "label": "Зеленоград 1", "flat": ""})
    ok(not any("Проверьте заказ" in m["text"] for m in r), "картой вне диалога адреса ничего не меняется")

    print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


asyncio.run(main())
