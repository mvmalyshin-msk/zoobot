"""Все клавиатуры. Формат callback_data: 'префикс:значение' (≤ 64 байт)."""
from aiogram.types import (
    InlineKeyboardButton as Btn,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

import texts
from config import config
from utils import rub


def _kb(rows: list[list[Btn]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- Согласие и меню ----------
def consent(policy_url: str) -> InlineKeyboardMarkup:
    """Без публичного адреса сайта ссылки нет: политику показываем текстом в чате (кнопка pol:show)."""
    policy_btn = (Btn(text="📄 Политика обработки ПДн", url=policy_url) if policy_url
                  else Btn(text="📄 Политика обработки ПДн", callback_data="pol:show"))
    return _kb([
        [policy_btn],
        [Btn(text="✅ Согласен на обработку ПДн", callback_data="pdn:agree")],  # отдельная кнопка
    ])


def main_menu(has_orders: bool, last_name: str | None = None) -> InlineKeyboardMarkup:
    rows = [[Btn(text="🐾 Заказать услугу", callback_data="menu:order")]]
    if has_orders:
        label = f"🔁 Повторить: {last_name}" if last_name else "🔁 Повторить последний заказ"
        rows.append([Btn(text=label[:60], callback_data="menu:repeat")])
        rows.append([Btn(text="📋 Мои заказы", callback_data="menu:orders")])
    rows.append([Btn(text="👤 Профиль", callback_data="menu:profile")])
    return _kb(rows)


def site_login(url: str) -> InlineKeyboardMarkup:
    return _kb([[Btn(text=texts.SITE_LOGIN_BUTTON, url=url)]])


def confirm_options(tg_url: str, yandex_url: str | None = None) -> InlineKeyboardMarkup:
    rows = [[Btn(text="✅ Подтвердить через Telegram", url=tg_url)]]
    if yandex_url:
        rows.append([Btn(text="✅ Подтвердить через Яндекс", url=yandex_url)])
    return _kb(rows)


def confirm_via_tg(url: str) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✅ Подтвердить через Telegram", url=url)]])


def offer_order() -> InlineKeyboardMarkup:
    """Бот сам предлагает следующий шаг: оформить заявку."""
    return _kb([[Btn(text="🐾 Заказать услугу", callback_data="menu:order")],
                [Btn(text="⬅️ В меню", callback_data="menu:home")]])


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


def traits_kb(selected: list[str]) -> InlineKeyboardMarkup:
    import traits
    rows = [[Btn(text=("✅ " if c in selected else "") + label[0].upper() + label[1:], callback_data=f"tr:{c}")]
            for c, label in traits.TRAITS]
    rows.append([Btn(text="Готово ➡️", callback_data="tr:done")])
    return _kb(rows)


def report_done(order_id: int) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✅ Готово, отчёт отправлен", callback_data=f"rp:done:{order_id}")]])


def no_notes() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="Ничего особенного", callback_data="pn:none")]])


def profile() -> InlineKeyboardMarkup:
    """Главный экран профиля: только главное, остальное в подменю."""
    return _kb([
        [Btn(text="✏️ Изменить данные", callback_data="profile:edit")],
        [Btn(text="🐾 Питомцы", callback_data="pets:list")],
        [Btn(text="⚙️ Настройки", callback_data="settings")],
        [Btn(text="⬅️ В меню", callback_data="menu:home")],
    ])


def settings(marketing_on: bool, tg_url: str | None = None, site_url: str | None = None,
             open_tg_url: str | None = None) -> InlineKeyboardMarkup:
    """Одинаковые настройки в боте и на сайте; отличаются только ссылки на другую площадку."""
    mk = "🔕 Отключить акции и новости" if marketing_on else "📣 Получать акции и новости"
    top = []
    if tg_url:
        top.append([Btn(text="✅ Подтвердить через Telegram", url=tg_url)])
    if open_tg_url:
        top.append([Btn(text="✈️ Открыть в Telegram", url=open_tg_url)])
    if site_url:
        top.append([Btn(text=texts.SITE_LOGIN_BUTTON, url=site_url)])
    return _kb(top + [
        [Btn(text="📞 Способ связи", callback_data="cp:menu")],
        [Btn(text=mk, callback_data="mk:off" if marketing_on else "mk:on")],
        [Btn(text="🗑 Удалить мои данные", callback_data="del:ask")],
        [Btn(text="⬅️ Профиль", callback_data="menu:profile")],
    ])


def pets_list(pets: list[dict]) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"🐾 {p['name']}", callback_data=f"pet:{p['id']}")] for p in pets]
    rows.append([Btn(text="➕ Добавить питомца", callback_data="profile:addpet")])
    rows.append([Btn(text="⬅️ Профиль", callback_data="menu:profile")])
    return _kb(rows)


def pet_card(pet_id: int) -> InlineKeyboardMarkup:
    return _kb([
        [Btn(text="✏️ Изменить", callback_data=f"pet:edit:{pet_id}"),
         Btn(text="🗑 Убрать", callback_data=f"pet:del:{pet_id}")],
        [Btn(text="⬅️ Питомцы", callback_data="pets:list")],
    ])


def pet_del_confirm(pet_id: int) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="Да, убрать", callback_data=f"pet:delyes:{pet_id}"),
                 Btn(text="Нет", callback_data=f"pet:{pet_id}")]])


# ---------- Заказ ----------
def services(items: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [Btn(text=f"{s['name']} — от {rub(s['base_price'])}", callback_data=f"svc:{s['id']}")]
        for s in items
    ]
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def service_categories(items: list[dict]) -> InlineKeyboardMarkup:
    """Первый экран выбора: только 4 услуги. Внутри каждой (кроме такси) — варианты."""
    rows = []
    for cat in texts.CATEGORY_ORDER:
        variants = [s for s in items if s["category"] == cat]
        if not variants:
            continue
        low = min(s["base_price"] for s in variants)
        data = f"svc:{variants[0]['id']}" if len(variants) == 1 else f"cat:{cat}"
        rows.append([Btn(text=f"{texts.CATEGORY_TITLES[cat]} — от {rub(low)}", callback_data=data)])
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def category_variants(items: list[dict], category: str) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"{texts.variant_label(category, s['name'])} — от {rub(s['base_price'])}",
                 callback_data=f"svc:{s['id']}")] for s in items if s["category"] == category]
    rows.append([Btn(text="⬅️ Назад", callback_data="menu:order")])
    return _kb(rows)


def taxi_pets(pets: list[dict]) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"🐾 {p['name']}", callback_data=f"tp:{p['id']}")] for p in pets]
    rows.append([Btn(text="➕ Другой питомец", callback_data="tp:new")])
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def taxi_warn() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✅ Понял(а), возьму", callback_data="tw:ok")]])


def taxi_for() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="👤 Для меня", callback_data="tf:me"),
                 Btn(text="👥 Для другого человека", callback_data="tf:other")]])


def quantity(values: list[int], other: bool = False) -> InlineKeyboardMarkup:
    rows = [[Btn(text=str(v), callback_data=f"qty:{v}") for v in values[i:i + 4]] for i in range(0, len(values), 4)]
    if other:
        rows.append([Btn(text="✏️ Другое", callback_data="qty:other")])
    return _kb(rows)


def use_profile_address(address: str) -> InlineKeyboardMarkup:
    short = address if len(address) <= 40 else address[:37] + "…"
    return _kb([[Btn(text=f"📍 {short}", callback_data="pa:profile")]])


def order_confirm() -> InlineKeyboardMarkup:
    """Первый экран заказа: подбирать специалиста или вносить правки (сами правки спрятаны за второй кнопкой)."""
    return _kb([
        [Btn(text="✅ Подтвердить и подобрать специалиста", callback_data="ord:ok")],
        [Btn(text="✏️ Внести правки", callback_data="ord:edit"),
         Btn(text="💬 Комментарий", callback_data="ord:comment")],
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


def my_orders(cancellable_ids: list[int], active_ids: list[int] = ()) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"❌ Отменить №{i}", callback_data=f"cancel:{i}")] for i in cancellable_ids]
    for i in active_ids:
        rows.append([Btn(text=f"⚠️ Проблема №{i}", callback_data=f"req:problem:{i}"),
                     Btn(text=f"🔁 Перенос №{i}", callback_data=f"req:move:{i}")])
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


def address_ask(point: str | None = None, frm: tuple[float, float] | None = None) -> ReplyKeyboardMarkup:
    """Кнопка «отправить геопозицию» под строкой ввода (один раз). point «a»/«b» — карта подпишет, какую точку
    выбирают (откуда забрать / куда поедем); frm — подача, чтобы показать её на карте при выборе назначения."""
    rows = []
    if config.webapp_url:  # мини-приложение с картой и подсказками адреса
        url = config.webapp_url.rstrip("/") + "/map"
        if point in ("a", "b"):
            url += f"?pt={point}"
            if frm and point == "b":
                url += f"&from={frm[0]},{frm[1]}"
        rows.append([KeyboardButton(text="🗺 Найти на карте", web_app=WebAppInfo(url=url))])
    rows.append([KeyboardButton(text="📍 Отправить геопозицию", request_location=True)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True, one_time_keyboard=True)


def address_options(labels: list[str], can_keep: bool, confirm: bool = False) -> InlineKeyboardMarkup:
    """confirm — адрес уже выбран на карте: кнопка «✅ Да, верно» вместо повтора адреса."""
    rows = [[Btn(text=(f"✅ Да, верно: {lb if len(lb) <= 40 else lb[:37] + '…'}" if confirm
                       else f"📍 {lb if len(lb) <= 55 else lb[:52] + '…'}"), callback_data=f"ad:{i}")]
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


def erase_confirm() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="🗑 Да, удалить мои данные", callback_data="del:yes")],
                [Btn(text="Отмена", callback_data="del:no")]])


def contact_pref(prefix: str = "cp") -> InlineKeyboardMarkup:
    return _kb([[Btn(text="📞 Звонок", callback_data=f"{prefix}:call"),
                 Btn(text="✉️ СМС", callback_data=f"{prefix}:sms"),
                 Btn(text="💬 Telegram", callback_data=f"{prefix}:telegram")]])


def recognize_skip() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="Пропустить", callback_data="rc:skip")]])


def erase_blocked(cancellable_ids: list[int], has_paid: bool) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"❌ Отменить заказ №{i}", callback_data=f"cancel:{i}")] for i in cancellable_ids]
    if has_paid:
        rows.append([Btn(text="📨 Попросить администратора", callback_data="del:ping")])
    rows.append([Btn(text="⬅️ В меню", callback_data="menu:home")])
    return _kb(rows)


def admin_new_order(order_id: int, chat_url: str) -> InlineKeyboardMarkup:
    rows = [[Btn(text="✅ Подтвердить", callback_data=f"ap:ok:{order_id}"),
             Btn(text="❌ Отклонить", callback_data=f"ap:no:{order_id}")],
            [_chat_btn(chat_url)]]
    if config.webapp_url:
        rows.append([Btn(text="🖥 Открыть в портале", url=f"{config.webapp_url}/portal/order/{order_id}")])
    return _kb(rows)


def pay_options(order_id: int, link: str | None, *, sbp: bool, card: bool) -> InlineKeyboardMarkup:
    rows = []
    if link:
        rows.append([Btn(text="💳 Оплатить по ссылке", url=link)])
    if sbp:
        rows.append([Btn(text="📱 Оплатить по СБП", callback_data=f"pay:sbp:{order_id}")])
    if card:
        rows.append([Btn(text="💳 Перевод на карту", callback_data=f"pay:card:{order_id}")])
    rows.append([Btn(text="✅ Я оплатил", callback_data=f"pay:claim:{order_id}")])
    return _kb(rows)


def admin_paid_check(order_id: int, chat_url: str) -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✅ Оплата пришла", callback_data=f"ap:paid:{order_id}"),
                 Btn(text="❌ Не вижу", callback_data=f"ap:nopay:{order_id}")],
                [_chat_btn(chat_url)]])


def admin_after_confirm(order_id: int, chat_url: str, *, methods: bool) -> InlineKeyboardMarkup:
    rows = []
    if methods:
        rows.append([Btn(text="📲 Отправить способы оплаты", callback_data=f"ap:methods:{order_id}")])
    rows.append([_chat_btn(chat_url)])
    return _kb(rows)


def admin_assign(order_id: int, executors: list[dict]) -> InlineKeyboardMarkup:
    rows = [[Btn(text=f"👷 {e['name']}", callback_data=f"ap:as:{order_id}:{e['id']}")] for e in executors]
    return _kb(rows or [[Btn(text="Нет подходящих исполнителей — /executors", callback_data="adm:execs")]])


def admin_menu() -> InlineKeyboardMarkup:
    return _kb([
        *([[Btn(text="🖥 Портал администратора", url=f"{config.webapp_url}/portal")]] if config.webapp_url else []),
        [Btn(text="📋 Активные заказы", callback_data="adm:orders")],
        [Btn(text="📊 Отчёт за сегодня", callback_data="adm:report")],
        [Btn(text="💰 Услуги и цены", callback_data="adm:services")],
        [Btn(text="⚙️ Наценки", callback_data="adm:rules")],
        [Btn(text="👷 Исполнители", callback_data="adm:execs")],
        [Btn(text="💳 Способы оплаты", callback_data="adm:pay")],
        [Btn(text="❓ Все команды", callback_data="adm:help")],
    ])


def link_choose(orders_: list[dict]) -> InlineKeyboardMarkup:
    return _kb([[Btn(text=f"№{o['id']} · {o['client_name'] or '—'} · {rub(o['price_total'])}",
                     callback_data=f"lnk:{o['id']}")] for o in orders_])


def rules_panel(rules: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for r in rules:
        unit = "₽" if r["effect_type"] == "fixed" else "%"
        rows.append([Btn(text=f"{r['name'][:18]} {r['value']:g}{unit}", callback_data="adm:rules"),
                     Btn(text="➖", callback_data=f"rl:dn:{r['id']}"), Btn(text="➕", callback_data=f"rl:up:{r['id']}"),
                     Btn(text="🗑", callback_data=f"rl:off:{r['id']}")])
    rows.append([Btn(text="⬅️ Админ-меню", callback_data="adm:home")])
    return _kb(rows)


def _chat_btn(url: str) -> Btn:
    """Кнопка «Чат с клиентом»: для Telegram — ссылка, для клиента из браузера — ответ через бота."""
    if url.startswith("web:"):
        return Btn(text="💬 Ответить клиенту", callback_data=f"wc:{url[4:]}")
    return Btn(text="💬 Чат с клиентом", url=url)


def reply_party(tg_id: int, portal: str | None) -> InlineKeyboardMarkup:
    """Под сообщением клиента или исполнителя: ответить через бота и открыть переписку в портале."""
    rows = [[Btn(text="💬 Ответить", callback_data=f"wc:{tg_id}")]]
    if portal:
        rows.append([Btn(text="🖥 Открыть в портале", url=f"{portal}/chat/{tg_id}")])
    return _kb(rows)


def relay_end() -> InlineKeyboardMarkup:
    return _kb([[Btn(text="✖️ Закончить чат", callback_data="wcx")]])


def chat_button(url: str) -> InlineKeyboardMarkup:
    return _kb([[_chat_btn(url)]])
