"""Все клавиатуры. Формат callback_data: 'префикс:значение' (≤ 64 байт)."""
from aiogram.types import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from config import config
from utils import rub


def _kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- Согласие и меню ----------
def consent(policy_url: str) -> InlineKeyboardMarkup:
    return _kb([
        [Btn(text="📄 Политика обработки ПДн", url=policy_url)],
        [Btn(text="✅ Согласен на обработку ПДн", callback_data="pdn:agree")],  # отдельная кнопка
    ])


def main_menu(has_orders: bool) -> InlineKeyboardMarkup:
    rows = [[Btn(text="🐾 Заказать услугу", callback_data="menu:order")]]
    if has_orders:
        rows.append([Btn(text="🔁 Повторить последний заказ", callback_data="menu:repeat")])
        rows.append([Btn(text="📋 Мои заказы", callback_data="menu:orders")])
    rows.append([Btn(text="👤 Профиль", callback_data="menu:profile")])
    return _kb(rows)


def back_to_menu() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="⬅️ В меню", callback_data="menu:home")]])


# ---------- Профиль ----------
def phone_request() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="📱 Отправить мой номер", request_contact=True)]],
        resize_keyboard=True, one_time_keyboard=True,
    )


def species() -> InlineKeyboardMarkup:
    return _kb([[
        Btn(text="🐕 Собака", callback_data="sp:собака"),
        Btn(text="🐈 Кошка", callback_data="sp:кошка"),
        Btn(text="🐾 Другое", callback_data="sp:другое"),
    ]])


def pet_size() -> InlineKeyboardMarkup:
    return _kb([[Btn(text=s, callback_data=f"sz:{s}") for s in ("S", "M", "L")]])


def no_notes() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="Ничего особенного", callback_data="pn:none")]])


def profile(marketing_on: bool) -> InlineKeyboardMarkup:
    mk = "🔕 Отключить рассылки" if marketing_on else "📣 Получать рассылки"
    return _kb([
        [Btn(text="✏️ Изменить данные", callback_data="profile:edit")],
        [Btn(text="➕ Добавить питомца", callback_data="profile:addpet")],
        [Btn(text=mk, callback_data="mk:off" if marketing_on else "mk:on")],
        [Btn(text="⬅️ В меню", callback_data="menu:home")],
    ])


# ---------- Заказ ----------
def services(items: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [Btn(text=f"{s['name']} — от {rub(s['base_price'])}", callback_data=f"svc:{s['id']}")]
        for s in items
    ]
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def quantity(values: list[int]) -> InlineKeyboardMarkup:
    return _kb([[Btn(text=str(v), callback_data=f"qty:{v}") for v in values]])


def use_profile_address(address: str) -> InlineKeyboardMarkup:
    short = address if len(address) <= 40 else address[:37] + "…"
    return _kb([[Btn(text=f"📍 {short}", callback_data="pa:profile")]])


def order_confirm() -> InlineKeyboardMarkup:
    """Первый экран заказа: подбирать специалиста или вносить правки (сами правки спрятаны за второй кнопкой)."""
    return _kb([
        [Btn(text="✅ Подтвердить и подобрать специалиста", callback_data="ord:ok")],
        [Btn(text="✏️ Внести правки", callback_data="ord:edit")],
        [Btn(text="❌ Отмена", callback_data="ord:cancel")],
    ])


def order_edit(outside_mkad: bool, many_pets: bool) -> InlineKeyboardMarkup:
    """Что можно поправить в заказе."""
    rows = [
        [Btn(text="🗓 Изменить время", callback_data="ord:when")],
        [Btn(text="✏️ Изменить адрес", callback_data="ord:addr")],
        [Btn(text=f"🛣 За МКАД: {'да' if outside_mkad else 'нет'}", callback_data="ord:mkad")],
    ]
    if many_pets:
        rows.append([Btn(text="🐕 Другой питомец", callback_data="ord:pet")])
    rows.append([Btn(text="◀ Назад к заказу", callback_data="ord:back")])
    return _kb(rows)


def pick_pet(pets: list[dict]) -> InlineKeyboardMarkup:
    return _kb([[Btn(text=f"{p['name']} ({p['species']})", callback_data=f"pick:{p['id']}")] for p in pets])


def my_orders(cancellable_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"❌ Отменить №{i}", callback_data=f"cancel:{i}")] for i in cancellable_ids]
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def cancel_confirm(order_id: int) -> InlineKeyboardMarkup:
    return _kb([[
        Btn(text="Да, отменить", callback_data=f"cancel_yes:{order_id}"),
        Btn(text="Нет", callback_data="menu:orders"),
    ]])


def pay_button(link: str) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="💳 Оплатить", url=link)]])


# ---------- Отзывы ----------
def rating(order_id: int) -> InlineKeyboardMarkup:
    return _kb([[Btn(text=f"{'⭐' * r}", callback_data=f"rev:{order_id}:{r}") for r in range(1, 6)]])


def skip_review() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="Пропустить", callback_data="rev_skip")]])


# ---------- Исполнитель ----------
def executor_start(order_id: int) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="▶️ Начал работу", callback_data=f"ex:start:{order_id}")]])


def executor_done(order_id: int) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✅ Завершил", callback_data=f"ex:done:{order_id}")]])


def address_ask() -> ReplyKeyboardMarkup:
    """Кнопка «отправить геопозицию» под строкой ввода (один раз)."""
    rows = []
    if config.webapp_url:  # мини-приложение с картой и подсказками адреса
        rows.append([KeyboardButton(text="🗺 Найти на карте", web_app=WebAppInfo(url=config.webapp_url))])
    rows.append([KeyboardButton(text="📍 Отправить геопозицию", request_location=True)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def address_options(labels: list[str], can_keep: bool) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"📍 {lb if len(lb) <= 55 else lb[:52] + '…'}", callback_data=f"ad:{i}")]
            for i, lb in enumerate(labels)]
    rows.append([Btn(text="✏️ Ввести заново", callback_data="ad:retry")])
    if can_keep:
        rows.append([Btn(text="Оставить как написано", callback_data="ad:keep")])
    return _kb(rows)


def breed_options(options: list[str], can_keep: bool) -> InlineKeyboardMarkup:
    rows = [[Btn(text=o, callback_data=f"pb:{i}")] for i, o in enumerate(options)]
    rows.append([Btn(text="✏️ Ввести заново", callback_data="pb:retry")])
    if can_keep:
        rows.append([Btn(text="Оставить как написано", callback_data="pb:keep")])
    return _kb(rows)
