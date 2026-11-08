"""Клиентская часть: согласие ПДн, профиль, заказ (≤ 4 экрана), мои заказы, повтор, отзывы."""
import logging
import os
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import breeds
import datepicker
import db
import geo
import keyboards as kb
import orders
import payinfo
import policy
import pricing
import repo
import ribbons
import talk
import taxi
import texts
import traits
from config import config
from utils import (
    fmt_local, from_db, normalize_phone, now_utc, parse_user_datetime, rub, to_db, to_local,
)

log = logging.getLogger(__name__)
router = Router()


class Profile(StatesGroup):
    name = State()
    phone = State()
    address = State()
    pet_name = State()
    pet_species = State()
    pet_breed = State()
    pet_size = State()
    pet_traits = State()
    pet_notes = State()
    contact = State()
    recognize = State()


class LinkPhone(StatesGroup):
    """Клиент с сайта подтвердил себя в Telegram и теперь делится своим номером кнопкой."""
    wait = State()


class Order(StatesGroup):
    when = State()
    qty = State()
    point_a = State()
    point_b = State()
    confirm = State()
    edit_address = State()
    comment = State()
    taxi_pet = State()
    taxi_warn = State()
    taxi_for = State()
    rider_name = State()
    rider_phone = State()


class Addr(StatesGroup):
    pick = State()  # клиент выбирает адрес из найденных на карте


class Review(StatesGroup):
    text = State()


# =====================================================================
# 1. Согласие на обработку ПДн, главное меню
# =====================================================================

async def show_consent(msg: Message) -> None:
    await msg.answer(texts.CONSENT_TEXT, reply_markup=kb.consent(policy.current_link()))


async def send_policy(msg: Message) -> None:
    for part in policy.chunks(policy.render_text(policy.operator_from_env(os.environ))):
        await msg.answer(part)


@router.message(Command("delete_me"))
async def cmd_delete_me(m: Message, state: FSMContext) -> None:
    await state.clear()
    await _erase_ask(m, m.from_user)


@router.message(F.text.func(texts.is_erase_request))
async def msg_erase(m: Message, state: FSMContext) -> None:
    await state.clear()
    await _erase_ask(m, m.from_user)


async def _erase_blocked(msg: Message, user_id: int) -> None:
    """Есть незавершённые заказы: не тупик, а кнопки — отменить неоплаченные или позвать администратора."""
    rows = [r for r in await repo.user_orders(user_id, 20) if r["status"] in orders.ACTIVE_STATUSES]
    cancellable = [r["id"] for r in rows if r["status"] in ("created", "confirmed")]
    has_paid = any(r["status"] not in ("created", "confirmed") for r in rows)
    await msg.answer(texts.ERASE_ACTIVE, reply_markup=kb.erase_blocked(cancellable, has_paid))


async def _erase_ask(msg: Message, tg_user) -> None:
    u = await repo.get_or_create_user(tg_user)
    if await repo.active_order_count(u["id"]):
        await _erase_blocked(msg, u["id"])
        return
    await msg.answer(texts.ERASE_ASK, reply_markup=kb.erase_confirm())


@router.callback_query(F.data == "del:ask")
async def cb_erase_ask(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await _erase_ask(cb.message, cb.from_user)


@router.callback_query(F.data == "del:ping")
async def cb_erase_ping(cb: CallbackQuery) -> None:
    await cb.answer()
    u = await repo.get_or_create_user(cb.from_user)
    ids = [f"№{r['id']}" for r in await repo.user_orders(u["id"], 20) if r["status"] in orders.ACTIVE_STATUSES]
    await orders.notify_admins(
        cb.bot, f"🗑 Клиент {u['name'] or cb.from_user.full_name} просит удалить данные, но есть незавершённые "
                f"заказы: {', '.join(ids) or '—'}. Решите вопрос с ним.",
        reply_markup=kb.chat_button(orders.chat_url(u["tg_username"], cb.from_user.id)))
    await cb.message.answer(texts.ERASE_PING_SENT)


@router.callback_query(F.data.in_({"del:yes", "del:no"}))
async def cb_erase(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if cb.data == "del:no":
        await cb.message.answer(texts.ERASE_CANCELLED, reply_markup=ReplyKeyboardRemove())
        return
    u = await repo.get_or_create_user(cb.from_user)
    if u.get("erased_at") and not (u["name"] or u["phone"] or u["pdn_consent"]):  # повторное нажатие: уже удалено, второе «Готово» не шлём
        return
    if await repo.active_order_count(u["id"]):  # за время раздумий мог появиться заказ
        await _erase_blocked(cb.message, u["id"])
        return
    await repo.erase_user(u["id"])
    await state.clear()
    await cb.message.answer(texts.ERASE_DONE, reply_markup=ReplyKeyboardRemove())  # панель карты/геопозиции больше не нужна


@router.message(Command("policy"))
async def cmd_policy(m: Message) -> None:
    await send_policy(m)


@router.callback_query(F.data == "pol:show")
async def cb_policy(cb: CallbackQuery) -> None:
    await cb.answer()
    await send_policy(cb.message)


def is_admin(tg_user) -> bool:
    return tg_user.id in config.admin_ids


async def show_menu(msg: Message, tg_user, text: str | None = None) -> None:
    if is_admin(tg_user):  # у админа меню только управления ботом, заказать с него нельзя
        await msg.answer(ribbons.tg_prefix(ribbons.ADMIN) + texts.ADMIN_MENU_TEXT, reply_markup=kb.admin_menu())
        return
    user = await repo.get_or_create_user(tg_user)
    last = next(iter(await repo.user_orders(user["id"], 1)), None)
    await msg.answer(ribbons.tg_prefix(ribbons.CLIENT) + (text or texts.menu_text(last)),
                     reply_markup=kb.main_menu(last is not None, last["name"] if last else None))


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("lg_")))
async def cmd_start_site_login(m: Message) -> None:
    """Клиент нажал «Войти через Telegram» на сайте: подтверждаем вход, страница откроется сама."""
    import webchat
    if await webchat.confirm_tg_login(command_args(m), m.from_user.id):
        await repo.get_or_create_user(m.from_user)
        await m.answer("✅ Готово! Вернитесь на сайт — кабинет откроется автоматически.")
    elif m.from_user.id in config.admin_ids:
        import webchat
        await m.answer(webchat.ADMIN_AS_CLIENT_TEXT)
    else:
        await m.answer("Ссылка входа устарела. Откройте сайт и нажмите «Войти через Telegram» ещё раз.")


def command_args(m: Message) -> str:
    parts = (m.text or "").split(maxsplit=1)
    return parts[1][3:] if len(parts) > 1 else ""


@router.message(CommandStart(deep_link=True, magic=F.args.startswith("wl_")))
async def cmd_start_web_link(m: Message, command: CommandObject, state: FSMContext) -> None:
    """П.4: клиент с сайта подтверждает себя через Telegram."""
    import webchat
    web_uid = await webchat.claim_tg_link(command.args[3:], m.from_user.id)
    if not web_uid:
        await m.answer(texts.TG_LINK_BAD)
        return
    await m.answer(texts.TG_LINK_OK_TG)
    await orders.notify(m.bot, web_uid, texts.TG_LINK_OK_WEB)
    row = await db.fetch_one("SELECT phone, phone_verified FROM users WHERE tg_id=?", (web_uid,))
    if row and row["phone"] and row["phone_verified"] != "telegram":
        await state.set_state(LinkPhone.wait)
        await state.update_data(web_uid=web_uid)
        await m.answer(texts.ASK_LINK_PHONE, reply_markup=kb.phone_request())


@router.message(LinkPhone.wait, F.contact)
async def link_phone_contact(m: Message, state: FSMContext) -> None:
    if m.contact.user_id != m.from_user.id:
        await m.answer(texts.PHONE_NOT_OWN, reply_markup=kb.phone_request())
        return
    data = await state.get_data()
    await state.clear()
    phone = normalize_phone(m.contact.phone_number)
    if not phone:
        await m.answer(texts.BAD_PHONE)
        return
    web_row = await db.fetch_one("SELECT id FROM users WHERE tg_id=?", (data["web_uid"],))
    if web_row:
        await repo.update_user(web_row["id"], phone=phone, phone_verified="telegram")
    await m.answer(texts.PHONE_VERIFIED_WEB.format(phone=phone))
    await orders.notify(m.bot, data["web_uid"], texts.PHONE_VERIFIED_WEB.format(phone=phone))


@router.message(LinkPhone.wait, F.text)
async def link_phone_text(m: Message, state: FSMContext) -> None:
    """Номер по ссылке принимаем только кнопкой: объясняем её, а не отвечаем общей фразой."""
    await m.answer(texts.PHONE_ONLY_BUTTON, reply_markup=kb.phone_request())


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(m.from_user)
    if not user["pdn_consent"] and not is_admin(m.from_user):
        await show_consent(m)  # без нажатия кнопки дальше не пускаем (см. ConsentMiddleware)
        return
    await show_menu(m, m.from_user)


@router.message(Command("menu"))
async def cmd_menu(m: Message, state: FSMContext) -> None:
    await state.clear()
    await show_menu(m, m.from_user)


@router.callback_query(F.data == "pdn:agree")
async def pdn_agree(cb: CallbackQuery) -> None:
    user = await repo.get_or_create_user(cb.from_user)
    if not user["pdn_consent"]:
        await repo.save_consent(user, "pdn", True, texts.PDN_VERSION)
    await cb.answer(texts.CONSENT_THANKS)
    await cb.message.edit_reply_markup(reply_markup=None)  # убираем кнопку, чтобы не жали повторно
    await show_menu(cb.message, cb.from_user)


@router.callback_query(F.data == "menu:home")
async def cb_home(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await show_menu(cb.message, cb.from_user)


# =====================================================================
# 2. Профиль клиента и питомца
# =====================================================================

async def show_profile(msg: Message, tg_user) -> None:
    user = await repo.get_or_create_user(tg_user)
    pets = await repo.list_pets(user["id"])
    lines = [
        "👤 Ваш профиль",
        f"Имя: {user['name'] or '—'}",
        f"Телефон: {user['phone'] or '—'}",
        f"Район: {user['district'] or '—'}",
        f"Адрес: {user['address'] or '—'}",
        "Связь: " + texts.CONTACT_LABELS.get(user["contact_pref"] or "telegram", "Telegram"),
        "",
        "Питомцы:",
    ]
    for p in pets:
        size = f", {p['size']}" if p["size"] else ""
        breed = f", {p['breed']}" if p["breed"] else ""
        notes = f"\n   Особенности: {p['notes']}" if p["notes"] else ""
        lines.append(f"• {p['name']} ({p['species']}{size}{breed}){notes}")
    if not pets:
        lines.append("— пока нет")
    await msg.answer("\n".join(lines), reply_markup=kb.profile())


async def show_settings(msg: Message, tg_user) -> None:
    user = await repo.get_or_create_user(tg_user)
    text = ("⚙️ Настройки\n"
            "Связь: " + texts.CONTACT_LABELS.get(user["contact_pref"] or "telegram", "Telegram") + "\n"
            "Уведомления о заказах: включены ✅\n"
            "Акции и новости: " + ("включены ✅" if user["marketing_consent"] else "выключены"))
    import webchat
    tg_url = site_url = open_tg_url = None
    if tg_user.id < 0:  # клиент с сайта: ссылка в Telegram — всегда, подтверждение — пока не сделано
        open_tg_url = f"https://t.me/{config.bot_username}"
        if user.get("verified_tg_id"):
            text += "\nПодтверждение: через Telegram ✅"
        else:
            text += "\nПодтверждение: ещё нет — нажмите кнопку, откроется Telegram"
            tg_url = await webchat.make_tg_link(tg_user.id)
    else:  # клиент из Telegram: кабинет на сайте, тот же самый
        site_url = await webchat.site_link_for(tg_user.id)
    await msg.answer(text, reply_markup=kb.settings(bool(user["marketing_consent"]), tg_url, site_url, open_tg_url))


async def show_pets(msg: Message, tg_user) -> None:
    user = await repo.get_or_create_user(tg_user)
    pets = await repo.list_pets(user["id"])
    text = "🐾 Ваши питомцы. Нажмите на питомца, чтобы изменить или убрать." if pets else "Питомцев пока нет."
    await msg.answer(text, reply_markup=kb.pets_list(pets))


def _pet_text(p: dict) -> str:
    size = f", {p['size']}" if p["size"] else ""
    breed = f", {p['breed']}" if p["breed"] else ""
    notes = f"\nОсобенности: {p['notes']}" if p["notes"] else ""
    return f"🐾 {p['name']} ({p['species']}{size}{breed}){notes}"


@router.callback_query(F.data == "settings")
async def cb_settings(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await show_settings(cb.message, cb.from_user)


@router.callback_query(F.data == "pets:list")
async def cb_pets(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await show_pets(cb.message, cb.from_user)


async def _own_pet(tg_user, pet_id: str) -> dict | None:
    user = await repo.get_or_create_user(tg_user)
    if not pet_id.isdigit():
        return None
    pet = await repo.get_pet(int(pet_id))
    return pet if pet and pet["user_id"] == user["id"] and pet["is_active"] else None


@router.callback_query(F.data.regexp(r"^pet:\d+$"))
async def cb_pet_card(cb: CallbackQuery) -> None:
    await cb.answer()
    pet = await _own_pet(cb.from_user, cb.data[4:])
    if not pet:
        await cb.message.answer(texts.STALE)
        return
    await cb.message.answer(_pet_text(pet), reply_markup=kb.pet_card(pet["id"]))


@router.callback_query(F.data.regexp(r"^pet:del:\d+$"))
async def cb_pet_del(cb: CallbackQuery) -> None:
    await cb.answer()
    pet = await _own_pet(cb.from_user, cb.data.split(":")[2])
    if not pet:
        await cb.message.answer(texts.STALE)
        return
    await cb.message.answer(f"Убрать питомца {pet['name']} из профиля? В старых заказах он останется.",
                            reply_markup=kb.pet_del_confirm(pet["id"]))


@router.callback_query(F.data.regexp(r"^pet:delyes:\d+$"))
async def cb_pet_delyes(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    if not await repo.deactivate_pet(int(cb.data.split(":")[2]), user["id"]):
        await cb.message.answer(texts.STALE)
        return
    await cb.message.answer("Убрал ✅")
    await show_pets(cb.message, cb.from_user)


@router.callback_query(F.data.regexp(r"^pet:edit:\d+$"))
async def cb_pet_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    pet = await _own_pet(cb.from_user, cb.data.split(":")[2])
    if not pet:
        await cb.message.answer(texts.STALE)
        return
    await start_profile(cb.message, state, after=None, add_pet_only=True)
    await state.update_data(pet_edit_id=pet["id"], back="pets")


@router.message(Command("profile"))
async def cmd_profile(m: Message, state: FSMContext) -> None:
    await state.clear()
    await show_profile(m, m.from_user)


@router.callback_query(F.data == "menu:profile")
async def cb_profile(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await show_profile(cb.message, cb.from_user)


@router.callback_query(F.data.in_({"mk:on", "mk:off"}))
async def cb_marketing(cb: CallbackQuery) -> None:
    """Отдельное согласие на рассылки: по умолчанию выключено, включается только этой кнопкой."""
    user = await repo.get_or_create_user(cb.from_user)
    on = cb.data == "mk:on"
    await repo.save_consent(user, "marketing", on, texts.MKT_VERSION)
    await cb.answer("Рассылки включены" if on else "Рассылки отключены")
    await show_settings(cb.message, cb.from_user)


def _from_web() -> bool:
    import webchat
    return webchat.ORIGIN.get() is not None


async def start_profile(msg: Message, state: FSMContext, after: str | None, add_pet_only: bool = False,
                        tg_user=None) -> None:
    """Запускает опрос. after='order' — потом сразу к выбору услуги.
    С tg_user опрос не повторяет то, что клиент уже сообщил: спрашивается только недостающее."""
    await state.clear()
    await state.update_data(after=after)
    if add_pet_only:
        await state.set_state(Profile.pet_name)
        await msg.answer(texts.ASK_PET_NAME)
    elif tg_user is not None and after is not None:
        user = await repo.get_or_create_user(tg_user)
        known = {k: user[k] for k in ("name", "phone", "address") if user[k]}
        if user["phone"]:
            await state.update_data(phone=user["phone"], phone_verified=user["phone_verified"])
        if user["name"]:
            await state.update_data(name=user["name"])
        if not known.get("name"):
            await state.set_state(Profile.name)
            await msg.answer(texts.ASK_NAME)
        elif not known.get("phone"):
            await state.set_state(Profile.phone)
            await msg.answer(texts.ASK_PHONE_WEB if _from_web() else texts.ASK_PHONE, reply_markup=kb.phone_request())
        elif not known.get("address"):
            await _ask_address(msg, state)
        else:
            await _after_address_block(msg, state, tg_user)
    else:
        await state.set_state(Profile.name)
        await msg.answer(texts.ASK_NAME)


@router.callback_query(F.data == "profile:edit")
async def cb_profile_edit(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await start_profile(cb.message, state, after=None)


@router.callback_query(F.data == "profile:addpet")
async def cb_profile_addpet(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await start_profile(cb.message, state, after=None, add_pet_only=True)
    await state.update_data(back="pets")


@router.message(Profile.name, F.text)
async def p_name(m: Message, state: FSMContext) -> None:
    name = breeds.check_person_name(m.text)
    if not name:
        await m.answer(texts.BAD_PERSON_NAME)
        return
    await state.update_data(name=name)
    if (await state.get_data()).get("phone"):  # телефон уже известен (вход по номеру) — повторно не спрашиваем
        await _ask_address(m, state)
        return
    await state.set_state(Profile.phone)
    import webchat
    ask = texts.ASK_PHONE_WEB if webchat.ORIGIN.get() is not None else texts.ASK_PHONE  # в браузере Telegram ни при чём
    await m.answer(ask, reply_markup=kb.phone_request())


@router.message(Profile.phone, F.contact)
async def p_phone_contact(m: Message, state: FSMContext) -> None:
    import webchat
    web = webchat.ORIGIN.get() is not None  # сообщение пришло из браузера, а не из Telegram
    own = m.contact.user_id == m.from_user.id
    if not web and m.from_user.id > 0 and not own:  # в Telegram принимаем только свой контакт: чужая карточка — не номер клиента
        await m.answer(texts.PHONE_NOT_OWN, reply_markup=kb.phone_request())
        return
    # из браузера номер приходит без user_id: Telegram его не подтверждал, но сессия уже вошла через Telegram или гость введёт сам
    await _save_phone(m, state, m.contact.phone_number, verified="telegram" if (own and not web) else None)


@router.message(Profile.phone, F.text)
async def p_phone_text(m: Message, state: FSMContext) -> None:
    import webchat
    if m.from_user.id > 0 and webchat.ORIGIN.get() is None:  # в Telegram номер набирают только кнопкой, иначе его не подтвердить
        if not normalize_phone(m.text):
            await m.answer(texts.BAD_PHONE)
        else:
            await m.answer(texts.PHONE_ONLY_BUTTON, reply_markup=kb.phone_request())
        return
    await _save_phone(m, state, m.text)


async def _save_phone(m: Message, state: FSMContext, raw: str, verified: str | None = None) -> None:
    phone = normalize_phone(raw)
    if not phone:
        await m.answer(texts.BAD_PHONE)
        return
    await state.update_data(phone=phone, phone_verified=verified)
    if verified == "telegram":
        me = await repo.get_or_create_user(m.from_user)
        known = await repo.find_profile_by_phone(phone, me["id"])
        if known:  # этот номер уже есть в базе: просим второй признак и подтягиваем данные
            await state.update_data(rc_from=known["id"], rc_fails=0)
            await state.set_state(Profile.recognize)
            await m.answer(texts.ASK_RECOGNIZE, reply_markup=kb.recognize_skip())
            return
    await _ask_address(m, state)


async def _ask_address(msg: Message, state: FSMContext) -> None:
    await state.set_state(Profile.address)
    await msg.answer(texts.ASK_ADDRESS + texts.MAP_HINT * bool(config.webapp_url), reply_markup=kb.address_ask())


async def _recognized(msg: Message, state: FSMContext, tg_user, text: str) -> bool:
    """Второй признак совпал: переносим профиль и питомцев. False — не совпало."""
    data = await state.get_data()
    old = await repo.get_user(data["rc_from"])
    if not old or not await repo.match_second_factor(old["id"], text):
        return False
    me = await repo.get_or_create_user(tg_user)
    await repo.copy_profile(old["id"], me["id"])
    await msg.answer(texts.RECOGNIZED.format(name=old["name"] or "друг"))
    await _after_address_block(msg, state, tg_user)
    return True


@router.message(Profile.recognize, F.text)
async def p_recognize(m: Message, state: FSMContext) -> None:
    if await _recognized(m, state, m.from_user, m.text):
        return
    fails = (await state.get_data()).get("rc_fails", 0) + 1
    await state.update_data(rc_fails=fails)
    if fails >= 2:  # не мучаем: обычный опрос дальше
        await _ask_address(m, state)
        return
    await m.answer(texts.RECOGNIZE_AGAIN, reply_markup=kb.recognize_skip())


@router.callback_query(Profile.recognize, F.data == "rc:skip")
async def cb_recognize_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _ask_address(cb.message, state)


# =====================================================================
# Проверка адреса по карте: клиент пишет адрес → бот показывает найденные варианты кнопками
# =====================================================================

ADDR_STATES = {Profile.address.state: "profile", Order.point_a.state: "a",
               Order.point_b.state: "b", Order.edit_address.state: "edit"}


def _ask_for(data: dict, target: str | None = None):
    """Клавиатура «Найти на карте» для нужной точки такси: карта подписывает, что выбирают, и показывает подачу."""
    target = target or data.get("addr_target")
    if target == "a":
        return kb.address_ask("a")
    if target == "b":
        return kb.address_ask("b", _pt(data.get("pt_a")))
    return kb.address_ask()


def _pick_prefix(target: str | None) -> str:
    """В такси два адреса: подсказываем, какой из них сейчас выбирают."""
    return {"a": texts.ADDR_PICK_A, "b": texts.ADDR_PICK_B}.get(target, "")


@router.message(Profile.address, F.text)
async def p_address(m: Message, state: FSMContext) -> None:
    await _address_input(m, state, m.text, "profile", Profile.address.state, m.from_user)


async def _address_input(m: Message, state: FSMContext, text: str, target: str, back: str, tg_user) -> None:
    text = " ".join(text.split())
    flat = geo.flat_only(text)
    if flat:  # «квартира 46» — это не адрес: на карте искать нечего
        data = await state.get_data()
        opts = [o for o in (data.get("addr_options") or []) if o.get("has_house")]
        if not opts:
            await m.answer(texts.FLAT_ONLY)
            return
        for o in opts:
            o["label"] = o["label"].split(", кв.")[0] + f", кв. {flat}"
        await state.update_data(addr_options=opts)
        await m.answer(texts.ADDR_FLAT_ADDED.format(flat=flat),
                       reply_markup=kb.address_options([o["label"] for o in opts], False))
        return
    if not geo.looks_like_address(text):
        await m.answer(texts.BAD_ADDRESS)
        return
    places = await geo.search(text)
    if places is None:  # карта недоступна — клиента не блокируем, но помечаем адрес как непроверенный
        await m.answer(texts.ADDR_MAP_DOWN)
        await _address_done(m, state, target, text[:200], None, None, False, tg_user)
        return
    data = await state.get_data()
    fails = data.get("addr_fails", 0)
    _, flat = geo.split_flat(text)
    opts = [{"label": p.label + (f", кв. {flat}" if flat else ""), "district": p.district,
             "outside": p.outside_mkad, "has_house": p.has_house, "lat": p.lat, "lon": p.lon} for p in places]
    await state.update_data(addr_target=target, addr_back=back, addr_text=text, addr_options=opts, addr_outside=False,
                            addr_fails=fails if opts else fails + 1)
    await state.set_state(Addr.pick)
    if opts:
        await m.answer(_pick_prefix(target) + texts.ADDR_PICK,
                       reply_markup=kb.address_options([o["label"] for o in opts], False))
    else:
        fails += 1
        diag = await geo.diagnose(text)
        elsewhere = bool(diag and diag.where)  # адрес в другом регионе — «оставить как написано» не предлагаем
        await state.update_data(addr_outside=elsewhere)
        await m.answer(texts.addr_not_found(diag, fails),
                       reply_markup=kb.address_options([], can_keep=fails >= 2 and not elsewhere))


@router.message(Addr.pick, F.text)
async def addr_pick_text(m: Message, state: FSMContext) -> None:
    """В режиме выбора клиент написал адрес заново — ищем заново."""
    data = await state.get_data()
    await _address_input(m, state, m.text, data["addr_target"], data["addr_back"], m.from_user)


@router.callback_query(Addr.pick, F.data.startswith("ad:"))
async def cb_addr(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    arg = cb.data[3:]
    if arg == "retry":
        await state.set_state(data["addr_back"])
        await cb.message.answer("Напишите адрес ещё раз.", reply_markup=_ask_for(data))
        return
    if arg == "keep":
        if data.get("addr_fails", 0) < 2 or not data.get("addr_text") or data.get("addr_outside"):
            await cb.message.answer(texts.ADDR_STALE)
            return
        await _address_done(cb.message, state, data["addr_target"], data["addr_text"][:200],
                            None, None, False, cb.from_user)
        return
    opts = data.get("addr_options") or []
    if not arg.isdigit() or int(arg) >= len(opts):
        await state.set_state(data.get("addr_back") or Profile.address.state)
        await cb.message.answer(texts.ADDR_STALE)
        return
    o = opts[int(arg)]
    if not o["has_house"]:
        await state.set_state(data["addr_back"])
        await cb.message.answer(texts.ADDR_NO_HOUSE, reply_markup=_ask_for(data))
        return
    await _address_done(cb.message, state, data["addr_target"], o["label"][:200], o["district"],
                        o["outside"], True, cb.from_user, lat=o.get("lat"), lon=o.get("lon"))


@router.message(StateFilter(Profile.address, Order.point_a, Order.point_b, Order.edit_address, Addr.pick),
                F.location)
async def addr_location(m: Message, state: FSMContext) -> None:
    cur = await state.get_state()
    data = await state.get_data()
    target = ADDR_STATES.get(cur) or data.get("addr_target")
    back = cur if cur in ADDR_STATES else data.get("addr_back")
    place = await geo.reverse(m.location.latitude, m.location.longitude)
    if not place:
        await m.answer(texts.ADDR_LOC_FAIL)
        return
    if not place.has_house:
        await m.answer(texts.ADDR_NO_HOUSE)
        return
    opts = [{"label": place.label, "district": place.district, "outside": place.outside_mkad, "has_house": True,
             "lat": m.location.latitude, "lon": m.location.longitude}]
    await state.update_data(addr_target=target, addr_back=back, addr_text=place.label, addr_options=opts,
                            addr_fails=0)
    await state.set_state(Addr.pick)
    await m.answer(_pick_prefix(target) + texts.ADDR_PICK, reply_markup=kb.address_options([place.label], False))


@router.message(StateFilter(Profile.address, Order.point_a, Order.point_b, Order.edit_address, Addr.pick),
                F.web_app_data)
async def addr_webapp(m: Message, state: FSMContext) -> None:
    """Адрес выбран в мини-приложении с картой. Координаты и текст перепроверяем, как и любой другой ввод."""
    cur = await state.get_state()
    data = await state.get_data()
    target = ADDR_STATES.get(cur) or data.get("addr_target")
    pick = geo.parse_pick(m.web_app_data.data)
    if not pick:
        await m.answer(texts.ADDR_WEB_BAD, reply_markup=_ask_for(data, target))
        return
    place = await geo.reverse(pick["lat"], pick["lon"])  # район и МКАД; если карта молчит — по координатам
    outside = place.outside_mkad if place else geo.outside_by_coords(pick["lat"], pick["lon"])
    label = pick["label"][:200]
    # клиент должен увидеть выбранный адрес и подтвердить его (или сразу ввести заново), а не ехать дальше вслепую
    back = cur if cur in ADDR_STATES else data.get("addr_back")
    opts = [{"label": label, "district": place.district if place else None, "outside": outside, "has_house": True,
             "lat": pick["lat"], "lon": pick["lon"]}]
    await state.update_data(addr_target=target, addr_back=back, addr_text=label, addr_options=opts,
                            addr_fails=0, addr_outside=False)
    await state.set_state(Addr.pick)
    check = {"a": texts.ADDR_CHECK_A, "b": texts.ADDR_CHECK_B}.get(target, texts.ADDR_CHECK)  # какой это адрес
    await m.answer(check.format(label=label), reply_markup=kb.address_options([label], False, confirm=True))


async def _address_done(msg: Message, state: FSMContext, target: str, label: str, district: str | None,
                        outside: bool | None, verified: bool, tg_user, lat: float | None = None,
                        lon: float | None = None) -> None:
    """Адрес принят (проверен картой или оставлен как есть). Идём дальше по сценарию."""
    await state.update_data(addr_fails=0, addr_options=[])
    if target == "profile":
        data = await state.get_data()
        user = await repo.get_or_create_user(tg_user)
        fields = {"name": data["name"], "phone": data["phone"], "phone_verified": data.get("phone_verified"),
                  "address": label, "address_verified": int(verified), "outside_mkad": int(bool(outside))}
        if district:
            fields["district"] = district
        await repo.update_user(user["id"], **fields)
        await state.set_state(Profile.contact)
        await msg.answer(texts.ASK_CONTACT, reply_markup=kb.contact_pref())
        return
    point = [lat, lon] if lat is not None and lon is not None else None  # координаты нужны такси для маршрута
    if target in ("a", "edit"):
        await state.update_data(address=label, out_a=bool(outside), unv_a=not verified, pt_a=point)
    else:
        await state.update_data(address_to=label, out_b=bool(outside), unv_b=not verified, pt_b=point)
    data = await state.get_data()
    await state.update_data(outside_mkad=bool(data.get("out_a")) or bool(data.get("out_b")))
    if target == "a":
        await state.set_state(Order.point_b)
        await msg.answer(texts.ASK_POINT_B, reply_markup=kb.address_ask("b", _pt(data.get("pt_a"))))
    else:
        await show_confirm(msg, state, tg_user)


async def _after_address_block(msg: Message, state: FSMContext, tg_user) -> None:
    """Имя, телефон, адрес и способ связи есть: если питомец уже известен — готово, иначе спрашиваем про питомца."""
    user = await repo.get_or_create_user(tg_user)
    if await repo.list_pets(user["id"]):
        await finish_profile(msg, state, tg_user)
    else:
        await state.set_state(Profile.pet_name)
        await msg.answer(texts.ASK_PET_NAME, reply_markup=ReplyKeyboardRemove())


@router.callback_query(F.data.startswith("cp:"))
async def cb_contact(cb: CallbackQuery, state: FSMContext) -> None:
    """Предпочтительный способ связи: при заполнении профиля и по кнопке в профиле."""
    await cb.answer()
    arg = cb.data[3:]
    if arg == "menu":
        await cb.message.answer(texts.ASK_CONTACT, reply_markup=kb.contact_pref())
        return
    if arg not in texts.CONTACT_LABELS:
        await cb.message.answer(texts.STALE)
        return
    user = await repo.get_or_create_user(cb.from_user)
    await repo.update_user(user["id"], contact_pref=arg)
    await cb.message.answer(texts.CONTACT_SAVED.format(how=texts.CONTACT_LABELS[arg]))
    if await state.get_state() == Profile.contact.state:
        await _after_address_block(cb.message, state, cb.from_user)
    else:
        await show_settings(cb.message, cb.from_user)


@router.message(Profile.pet_name, F.text)
async def p_pet_name(m: Message, state: FSMContext) -> None:
    name = breeds.check_pet_name(m.text)
    if not name:
        await m.answer(texts.BAD_PET_NAME)
        return
    await state.update_data(pet_name=name)
    await state.set_state(Profile.pet_species)
    await m.answer(texts.ASK_PET_SPECIES, reply_markup=kb.species())


async def _set_species(msg: Message, state: FSMContext, species: str) -> None:
    await state.update_data(pet_species=species, pet_breed=None, breed_unverified=False)
    await state.set_state(Profile.pet_breed)
    await msg.answer(texts.ASK_PET_OTHER if species == "другое" else texts.ASK_PET_BREED)


@router.callback_query(Profile.pet_species, F.data.startswith("sp:"))
async def p_pet_species(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _set_species(cb.message, state, cb.data.split(":", 1)[1])


@router.message(Profile.pet_species, F.text)
async def p_pet_species_text(m: Message, state: FSMContext) -> None:
    species = breeds.parse_species(m.text)  # «кошка», «пёс» словами тоже понятны
    if not species:
        await m.answer(texts.HINT_SPECIES)
        return
    await _set_species(m, state, species)


def _check_breed(species: str, text: str) -> breeds.Verdict:
    return breeds.check_other_animal(text) if species == "другое" else breeds.check_breed(species, text)


@router.message(Profile.pet_breed, F.text)
async def p_pet_breed(m: Message, state: FSMContext) -> None:
    data = await state.get_data()
    species, text = data["pet_species"], " ".join(m.text.split())
    v = _check_breed(species, text)
    if v.kind in ("ok", "mixed"):
        await _accept_breed(m, state, v.canonical, v.size)
    elif v.kind == "suggest":
        await state.update_data(pb_options=list(v.options), pb_text=text)
        await m.answer(texts.BREED_SUGGEST, reply_markup=kb.breed_options(list(v.options), False))
    elif v.kind == "unknown":
        await state.update_data(pb_options=[], pb_text=v.canonical or text)
        await m.answer(texts.OTHER_UNKNOWN if species == "другое" else texts.BREED_UNKNOWN,
                       reply_markup=kb.breed_options([], True))
    elif v.kind == "wrong_species":
        await state.set_state(Profile.pet_species)
        await m.answer(f"«{text}» — это {v.other_species}, а вы выбрали «{species}». "
                       "Выберите вид питомца заново:", reply_markup=kb.species())
    else:
        await m.answer(texts.BAD_OTHER if species == "другое" else texts.BAD_BREED)


async def _accept_breed(msg: Message, state: FSMContext, canonical: str, size_hint: str | None,
                        unverified: bool = False) -> None:
    data = await state.get_data()
    if data["pet_species"] == "другое":  # вид сохраняем конкретный: «хомяк», а не «другое»
        await state.update_data(pet_species=canonical.lower(), pet_breed=None)
    else:
        await state.update_data(pet_breed=canonical)
    await state.update_data(breed_unverified=unverified)
    await state.set_state(Profile.pet_size)
    hint = f"\nОбычно такие — {size_hint}." if size_hint else ""
    await msg.answer(texts.ASK_PET_SIZE + hint, reply_markup=kb.pet_size())


@router.callback_query(Profile.pet_breed, F.data.startswith("pb:"))
async def cb_breed(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    arg = cb.data[3:]
    species = data["pet_species"]
    if arg == "retry":
        await cb.message.answer(texts.ASK_PET_OTHER if species == "другое" else texts.ASK_PET_BREED)
    elif arg == "keep":
        text = data.get("pb_text")
        if not text:
            await cb.message.answer(texts.ASK_PET_OTHER if species == "другое" else texts.ASK_PET_BREED)
            return
        await _accept_breed(cb.message, state, text[:60], None, unverified=True)
    elif arg.isdigit() and int(arg) < len(data.get("pb_options") or []):
        name = data["pb_options"][int(arg)]
        await _accept_breed(cb.message, state, name, _check_breed(species, name).size)
    else:
        await cb.message.answer(texts.ASK_PET_OTHER if species == "другое" else texts.ASK_PET_BREED)


async def _set_size(msg: Message, state: FSMContext, size: str) -> None:
    await state.update_data(pet_size=size, pet_traits=[])
    if (await state.get_data()).get("pet_species") == "собака":  # анкета повадок только для собак
        await state.set_state(Profile.pet_traits)
        await msg.answer(texts.ASK_TRAITS, reply_markup=kb.traits_kb([]))
        return
    await state.set_state(Profile.pet_notes)
    await msg.answer(texts.ASK_PET_NOTES, reply_markup=kb.no_notes())


@router.callback_query(Profile.pet_traits, F.data.startswith("tr:"))
async def cb_traits(cb: CallbackQuery, state: FSMContext) -> None:
    code = cb.data[3:]
    if code == "done":
        await cb.answer()
        await state.set_state(Profile.pet_notes)
        await cb.message.answer(texts.ASK_PET_NOTES, reply_markup=kb.no_notes())
        return
    if code not in traits.LABELS:
        await cb.answer("Не получилось, нажмите ещё раз")
        return
    await cb.answer()
    chosen = traits.toggle((await state.get_data()).get("pet_traits") or [], code)
    await state.update_data(pet_traits=chosen)
    await _edit(cb.message, texts.ASK_TRAITS, kb.traits_kb(chosen))


@router.callback_query(Profile.pet_size, F.data.startswith("sz:"))
async def p_pet_size(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _set_size(cb.message, state, cb.data.split(":", 1)[1])


@router.message(Profile.pet_size, F.text)
async def p_pet_size_text(m: Message, state: FSMContext) -> None:
    size = breeds.parse_size(m.text)
    if not size:
        await m.answer(texts.HINT_SIZE)
        return
    await _set_size(m, state, size)


@router.callback_query(Profile.pet_notes, F.data == "pn:none")
async def p_pet_notes_none(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _save_pet(cb.message, cb.from_user, state, None)


@router.message(Profile.pet_notes, F.text)
async def p_pet_notes(m: Message, state: FSMContext) -> None:
    await _save_pet(m, m.from_user, state, m.text.strip()[:500])


async def _save_pet(msg: Message, tg_user, state: FSMContext, notes: str | None) -> None:
    data = await state.get_data()
    user = await repo.get_or_create_user(tg_user)
    if data.get("breed_unverified"):  # порода/вид не из справочника — администратор уточнит
        what = data.get("pet_breed") or data["pet_species"]
        notes = f"Не из справочника, уточнить: {what}. " + (notes or "")
    args = (data["pet_name"], data["pet_species"], data.get("pet_breed"),
            data["pet_size"], notes.strip() if notes else None, traits.pack(data.get("pet_traits") or []))
    if data.get("pet_edit_id"):
        pet_id = data["pet_edit_id"]
        await repo.update_pet(pet_id, user["id"], *args)
    else:
        pet_id = await repo.add_pet(user["id"], *args)
    await finish_profile(msg, state, tg_user, pet_id)


async def site_login_markup(tg_id: int):
    """Кнопка «Открыть на сайте»: одноразовая ссылка входа в кабинет этого же Telegram-аккаунта."""
    import webchat
    link = await webchat.site_link_for(tg_id)
    return kb.site_login(link) if link else None


async def _registration_confirm(msg: Message, tg_user, data: dict) -> None:
    name, phone = data.get("name") or "—", data.get("phone")
    if tg_user.id > 0:  # Telegram уже проверил, что это реальный аккаунт
        await msg.answer(texts.REG_DONE_TG.format(name=name, phone=phone),
                         reply_markup=await site_login_markup(tg_user.id))
        return
    import phoneverify
    import webchat
    from config import config
    ya = f"{config.webapp_url}/verify/yandex" if phoneverify.enabled() else None
    await msg.answer(texts.REG_DONE_WEB.format(name=name, phone=phone),
                     reply_markup=kb.confirm_options(await webchat.make_tg_link(tg_user.id), ya))


async def finish_profile(msg: Message, state: FSMContext, tg_user=None, pet_id: int | None = None) -> None:
    tg_user = tg_user or msg.from_user
    data = await state.get_data()
    after = data.get("after")
    await state.clear()
    if data.get("phone"):  # закончена регистрация (а не только питомец): подтверждение клиенту
        await _registration_confirm(msg, tg_user, data)
    if after == "order":
        await show_services(msg)
    elif isinstance(after, str) and after.startswith("taxi:") and pet_id:
        user = await repo.get_or_create_user(tg_user)
        service = await db.fetch_one("SELECT * FROM services WHERE id=?", (int(after[5:]),))
        await _init_order(state, user, service, pet_id)
        await _taxi_warn(msg, state, await repo.get_pet(pet_id))
    elif data.get("back") == "pets":
        await msg.answer("Сохранил ✅ Хотите сразу заказать услугу?", reply_markup=kb.offer_order())
        await show_pets(msg, tg_user)
    else:
        await msg.answer("Сохранил ✅ Хотите сразу заказать услугу?", reply_markup=kb.offer_order())
        await show_profile(msg, tg_user)


# =====================================================================
# 3. Сценарий заказа: услуга → дата/длительность → подтверждение (+ цена)
# =====================================================================

async def show_services(msg: Message) -> None:
    items = await db.fetch_all("SELECT * FROM services WHERE active=1 ORDER BY id")
    await msg.answer(texts.CHOOSE_SERVICE, reply_markup=kb.service_categories(items))


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery) -> None:
    """Второй уровень: внутри услуги — время, уровень, приходящая или с проживанием и т. д."""
    await cb.answer()
    cat = cb.data[4:]
    items = await db.fetch_all("SELECT * FROM services WHERE active=1 AND category=? ORDER BY id", (cat,))
    if cat not in texts.CATEGORY_TITLES or not items:
        await cb.message.answer("Эта услуга сейчас недоступна.")
        return
    await _edit(cb.message, texts.CHOOSE_VARIANT.format(title=texts.CATEGORY_TITLES[cat]),
                kb.category_variants(items, cat))


@router.callback_query(F.data == "menu:order")
async def cb_order(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if is_admin(cb.from_user):
        await show_menu(cb.message, cb.from_user)
        return
    user = await repo.get_or_create_user(cb.from_user)
    # при первом заказе собираем профиль и питомца, потом возвращаемся к выбору услуги
    if not repo.profile_complete(user) or not await repo.list_pets(user["id"]):
        await start_profile(cb.message, state, after="order", tg_user=cb.from_user)
        return
    await state.clear()
    await show_services(cb.message)


@router.callback_query(F.data.startswith("svc:"))
async def cb_service(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    if is_admin(cb.from_user):
        await show_menu(cb.message, cb.from_user)
        return
    service = await db.fetch_one("SELECT * FROM services WHERE id=? AND active=1",
                                 (int(cb.data.split(":")[1]),))
    if not service:
        await cb.message.answer("Эта услуга сейчас недоступна.")
        return
    user = await repo.get_or_create_user(cb.from_user)
    pets = await repo.list_pets(user["id"])
    if not pets or not repo.profile_complete(user):
        await start_profile(cb.message, state, after="order", tg_user=cb.from_user)
        return
    await _init_order(state, user, service, pets[0]["id"])
    if service["category"] == "taxi":  # сначала: кто поедет, предупреждение, для кого заказ
        await state.set_state(Order.taxi_pet)
        await cb.message.answer(texts.TAXI_WHO, reply_markup=kb.taxi_pets(pets))
        return
    await state.set_state(Order.when)
    await ask_when(cb.message, service)


async def _init_order(state: FSMContext, user: dict, service: dict, pet_id: int) -> None:
    await state.clear()
    await state.update_data(service_id=service["id"], pet_id=pet_id, quantity=1,
                            address=user["address"], address_to=None,
                            outside_mkad=bool(user["outside_mkad"]), out_a=bool(user["outside_mkad"]),
                            out_b=False, unv_a=not user["address_verified"], unv_b=False, prefilled=False)


# ---- Зоотакси: питомец, предупреждение, для кого ----

async def _taxi_warn(msg: Message, state: FSMContext, pet: dict) -> None:
    await state.update_data(pet_id=pet["id"])
    await state.set_state(Order.taxi_warn)
    dog = pet["species"] == "собака"
    await msg.answer(texts.TAXI_WARN_DOG if dog else texts.TAXI_WARN_CARRIER, reply_markup=kb.taxi_warn())


@router.callback_query(Order.taxi_pet, F.data == "tp:new")
async def cb_taxi_new_pet(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    sid = (await state.get_data())["service_id"]
    await start_profile(cb.message, state, after=f"taxi:{sid}", add_pet_only=True)


@router.callback_query(Order.taxi_pet, F.data.regexp(r"^tp:\d+$"))
async def cb_taxi_pet(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    pet = await _own_pet(cb.from_user, cb.data[3:])
    if not pet:
        await cb.message.answer(texts.STALE)
        return
    await _taxi_warn(cb.message, state, pet)


@router.callback_query(Order.taxi_warn, F.data == "tw:ok")
async def cb_taxi_warn_ok(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Order.taxi_for)
    await cb.message.answer(texts.TAXI_FOR, reply_markup=kb.taxi_for())


async def _taxi_to_when(msg: Message, state: FSMContext) -> None:
    await state.set_state(Order.when)
    await ask_when(msg, await _service_of(state))


@router.callback_query(Order.taxi_for, F.data == "tf:me")
async def cb_taxi_for_me(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.update_data(rider_name=None, rider_phone=None)
    await _taxi_to_when(cb.message, state)


@router.callback_query(Order.taxi_for, F.data == "tf:other")
async def cb_taxi_for_other(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Order.rider_name)
    await cb.message.answer(texts.TAXI_RIDER_NAME)


@router.message(Order.rider_name, F.text)
async def msg_rider_name(m: Message, state: FSMContext) -> None:
    name = breeds.check_person_name(m.text)
    if not name:
        await m.answer(texts.BAD_PERSON_NAME)
        return
    await state.update_data(rider_name=name)
    await state.set_state(Order.rider_phone)
    await m.answer(texts.TAXI_RIDER_PHONE)


@router.message(Order.rider_phone, F.text)
async def msg_rider_phone(m: Message, state: FSMContext) -> None:
    phone = normalize_phone(m.text)
    if not phone:
        await m.answer(texts.BAD_PHONE)
        return
    await state.update_data(rider_phone=phone)
    await _taxi_to_when(m, state)


def min_ahead(service) -> timedelta:
    """Запас до начала: такси можно заказать почти сразу, для остальных услуг нужно время найти исполнителя."""
    return timedelta(minutes=30) if service["category"] == "taxi" else timedelta(days=1)


async def _service_of(state: FSMContext) -> dict:
    return await db.fetch_one("SELECT * FROM services WHERE id=?", ((await state.get_data())["service_id"],))


def too_soon_text(service) -> str:
    if service["category"] == "taxi":
        return texts.PAST_WHEN
    earliest = datepicker.first_allowed(to_local(now_utc()) + min_ahead(service))  # ночь — на утро
    return texts.TOO_SOON.format(earliest=earliest.strftime("%d.%m.%Y %H:%M"))


async def ask_when(msg: Message, service, prefix: str = "") -> None:
    """Спрашивает дату и время: окно с календарём, а текстом тоже можно."""
    if service["category"] == "taxi":
        text = texts.ASK_WHEN_TAXI
    elif service["unit"] == "сутки":
        text = texts.ASK_WHEN_START
    else:
        text = texts.ASK_WHEN
    text = prefix + text
    now = to_local(now_utc())
    await msg.answer(text, reply_markup=datepicker.month_markup(now.year, now.month, now, min_ahead(service)))


async def _accept_when(msg: Message, state: FSMContext, tg_user, dt) -> bool:
    """Проверяет выбранное время и идёт дальше по сценарию. False — время не подошло."""
    now = to_local(now_utc())
    data = await state.get_data()
    service = await db.fetch_one("SELECT * FROM services WHERE id=?", (data["service_id"],))
    if dt < now + min_ahead(service):  # сначала «слишком рано»: ответ называет самое раннее время в рабочие часы
        await msg.answer(too_soon_text(service))
        return False
    if not datepicker.in_work_hours(dt):  # текстом тоже нельзя обойти рабочие часы
        await msg.answer(texts.OUT_OF_HOURS)
        return False
    if dt > now + timedelta(days=366):
        await msg.answer(texts.FAR_WHEN)
        return False
    await state.update_data(start_at=to_db(dt))

    if data.get("prefilled"):  # «повторить заказ»: остальное уже известно
        await show_confirm(msg, state, tg_user)
    elif service["unit"] == "час":
        await state.set_state(Order.qty)
        await msg.answer(texts.ASK_HOURS, reply_markup=kb.quantity([1, 2, 3, 4, 6, 8, 10, 12], other=True))
    elif service["unit"] == "сутки":
        await state.set_state(Order.qty)
        await msg.answer(texts.ASK_NIGHTS, reply_markup=kb.quantity([1, 2, 3, 5, 7, 10, 14, 21], other=True))
    elif service["category"] == "taxi":
        user = await repo.get_or_create_user(tg_user)
        await state.set_state(Order.point_a)
        if user["address"]:  # сохранённый адрес — одной кнопкой; другой — текстом, картой или геопозицией
            await msg.answer(texts.ASK_POINT_A, reply_markup=kb.use_profile_address(user["address"]))
            await msg.answer(texts.ASK_POINT_A_OTHER, reply_markup=kb.address_ask("a"))
        else:
            await msg.answer(texts.ASK_POINT_A_NO_SAVED, reply_markup=kb.address_ask("a"))
    else:
        await show_confirm(msg, state, tg_user)
    return True


@router.message(Order.when, F.text)
async def got_when(m: Message, state: FSMContext) -> None:
    dt = parse_user_datetime(m.text)
    if dt is None:
        await m.answer(texts.BAD_WHEN)
        return
    await _accept_when(m, state, m.from_user, dt)


async def _edit(msg: Message, text: str, markup=None) -> None:
    """Меняет окно выбора на месте; если Telegram не дал (сообщение старое), пишет новое."""
    try:
        await msg.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            await msg.answer(text, reply_markup=markup)


@router.callback_query(Order.when, F.data.startswith("cal:"))
async def cb_calendar(cb: CallbackQuery, state: FSMContext) -> None:
    parsed = datepicker.parse(cb.data)
    if parsed is None:
        await cb.answer("Не получилось, выберите ещё раз")
        return
    kind, value, hour = parsed
    now = to_local(now_utc())
    service = await _service_of(state)
    ahead = min_ahead(service)
    if kind == "x":
        await cb.answer()
    elif kind == "m":
        await cb.answer()
        await _edit(cb.message, texts.PICK_DATE, datepicker.month_markup(value.year, value.month, now, ahead))
    elif kind == "d":
        await cb.answer()
        await _edit(cb.message, texts.PICK_HOUR.format(day=value.strftime("%d.%m.%Y")),
                    datepicker.hours_markup(value, now, ahead))
    elif kind == "h":
        await cb.answer()
        await _edit(cb.message, texts.PICK_MINUTES.format(day=value.strftime("%d.%m.%Y")),
                    datepicker.minutes_markup(value, hour, now, ahead))
    else:  # "t": время выбрано целиком
        if not datepicker.allowed(value, now, ahead):
            why = texts.OUT_OF_HOURS if not datepicker.in_work_hours(value) else too_soon_text(service)
            await cb.answer(why, show_alert=True)  # пока выбирали, время ушло (или кнопка подделана)
            await _edit(cb.message, texts.PICK_DATE, datepicker.month_markup(now.year, now.month, now, ahead))
            return
        await cb.answer()
        await _edit(cb.message, f"🗓 Когда: {fmt_local(value)}")
        await _accept_when(cb.message, state, cb.from_user, value)


@router.callback_query(F.data.startswith("cal:"))
async def cb_calendar_stale(cb: CallbackQuery) -> None:
    """Нажали кнопку из старого окна календаря, когда заказ уже дальше или отменён."""
    await cb.answer("Это окно уже не нужно. Начните заказ заново: /menu", show_alert=True)


async def _set_qty(msg: Message, tg_user, state: FSMContext, raw: str) -> None:
    data = await state.get_data()
    service = await db.fetch_one("SELECT * FROM services WHERE id=?", (data["service_id"],))
    limit = 24 if service["unit"] == "час" else 30
    if not raw.isdigit() or not 1 <= int(raw) <= limit:
        await msg.answer(f"{texts.BAD_QTY} (от 1 до {limit})")
        return
    await state.update_data(quantity=int(raw))
    await show_confirm(msg, state, tg_user)


@router.callback_query(Order.qty, F.data == "qty:other")
async def cb_qty_other(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer(texts.ASK_QTY_OTHER)


@router.callback_query(Order.qty, F.data.startswith("qty:"))
async def cb_qty(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _set_qty(cb.message, cb.from_user, state, cb.data.split(":")[1])


@router.message(Order.qty, F.text)
async def msg_qty(m: Message, state: FSMContext) -> None:
    await _set_qty(m, m.from_user, state, m.text.strip())


# ---- Зоотакси: точка А и Б ----

@router.callback_query(Order.point_a, F.data == "pa:profile")
async def cb_point_a_profile(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    await state.update_data(address=user["address"], out_a=bool(user["outside_mkad"]),
                            unv_a=not user["address_verified"], pt_a=None)  # координат у сохранённого адреса нет — найдём при расчёте
    data = await state.get_data()
    await state.update_data(outside_mkad=bool(data.get("out_a")) or bool(data.get("out_b")))
    await state.set_state(Order.point_b)
    await cb.message.answer(texts.ASK_POINT_B, reply_markup=kb.address_ask("b"))


@router.message(Order.point_a, F.text)
async def msg_point_a(m: Message, state: FSMContext) -> None:
    await _address_input(m, state, m.text, "a", Order.point_a.state, m.from_user)


@router.message(Order.point_b, F.text)
async def msg_point_b(m: Message, state: FSMContext) -> None:
    await _address_input(m, state, m.text, "b", Order.point_b.state, m.from_user)


# ---- Экран подтверждения с расчётом цены ----

def compute_end(service: dict, start, qty: int):
    """Конец слота: для часов/суток по количеству, для выгула по длительности из кода услуги."""
    if service["unit"] == "час":
        return start + timedelta(hours=qty)
    if service["unit"] == "сутки":
        return start + timedelta(days=qty)
    if service["code"] == "walk30":
        return start + timedelta(minutes=30)
    if service["code"] == "walk60":
        return start + timedelta(minutes=60)
    return None


def _pt(raw) -> tuple[float, float] | None:
    """Точка из данных диалога ([lat, lon]) → кортеж."""
    try:
        return float(raw[0]), float(raw[1])
    except (TypeError, IndexError, ValueError):
        return None


async def _geocode_point(label: str | None) -> tuple[float, float] | None:
    """Координаты адреса без сохранённой точки (сохранённый в профиле адрес, «оставить как написано»)."""
    if not label:
        return None
    places = await geo.search(label)
    for p in places or []:
        if p.has_house:
            return p.lat, p.lon
    return None


async def _ensure_route(state: FSMContext) -> dict:
    """Для такси: маршрут от подачи до места назначения считается один раз на пару точек и хранится в данных диалога."""
    data = await state.get_data()
    if not data.get("address_to"):
        return data
    a, b = _pt(data.get("pt_a")), _pt(data.get("pt_b"))
    if a is None:
        a = await _geocode_point(data.get("address"))
    if b is None:
        b = await _geocode_point(data.get("address_to"))
    if a is None or b is None:  # без координат цену по счётчику не посчитать — покажем посадку и животное
        await state.update_data(pt_a=list(a) if a else None, pt_b=list(b) if b else None, route=None, route_key=None)
        return await state.get_data()
    key = [list(a), list(b)]
    if data.get("route") and data.get("route_key") == key:
        return data
    r = await taxi.route(a, b)
    await state.update_data(pt_a=list(a), pt_b=list(b), route=r.as_dict(), route_key=key)
    return await state.get_data()


async def _price_for(data: dict) -> tuple[int, list, dict, dict]:
    """Считает цену по данным из FSM. Возвращает (итог, разбивка, услуга, питомец)."""
    service = await db.fetch_one("SELECT * FROM services WHERE id=?", (data["service_id"],))
    pet = await repo.get_pet(data["pet_id"])
    start = from_db(data["start_at"])
    params = {
        "quantity": data["quantity"],
        "start_at": start,
        "pet_size": pet["size"] if pet and pet["species"] == "собака" else None,
        "is_urgent": to_local(start).date() == to_local(now_utc()).date(),  # «сегодня»
        "outside_mkad": bool(data.get("outside_mkad")),
        "pet_traits": pet["traits"] if pet else None,
        "pet_notes": (pet["notes"] or "") if pet else "",
        "pet_species": pet["species"] if pet else None,
        "route": data.get("route"),
    }
    total, breakdown = await pricing.calculate_price(service["id"], params)
    return total, breakdown, service, pet, params


async def show_confirm(msg: Message, state: FSMContext, tg_user) -> None:
    data = await _ensure_route(state)
    total, breakdown, service, pet, _ = await _price_for(data)
    user = await repo.get_or_create_user(tg_user)
    pets = await repo.list_pets(user["id"])
    start = from_db(data["start_at"])
    end = compute_end(service, start, data["quantity"])

    when = fmt_local(start) + (f" → {fmt_local(end)}" if end and service["unit"] in ("час", "сутки") else "")
    lines = ["Проверьте заказ:", "", f"Услуга: {service['name']}", f"Когда: {when}"]
    if data.get("address_to"):
        lines += [f"Подача: {data['address']}", f"Куда: {data['address_to']}"]
        route = taxi.Route.of(data.get("route"))
        if route:
            lines.append(f"Маршрут: ≈ {taxi.fmt_km(route.km)} км, ≈ {route.minutes} мин в пути"
                         + ("" if route.by_road else " (оценка по прямой: карта маршрутов не ответила)"))
    else:
        lines.append(f"Адрес: {data['address']}")
    if pet:
        size = f", {pet['size']}" if pet["size"] else ""
        lines.append(f"Питомец: {pet['name']} ({pet['species']}{size})")
    if data.get("rider_name"):
        lines.append(f"Поедет: {data['rider_name']}, {data['rider_phone']}")
    if service["category"] == "taxi":
        lines.append("⚠️ Возьмите намордник (для собак) или переноску и подстилку." if pet and pet["species"] == "собака"
                     else "⚠️ Возьмите переноску или клетку и подстилку.")
    if data.get("comment"):
        lines.append(f"Комментарий: {data['comment']}")
    lines += ["", "💰 " + pricing.format_range(total), "Что учтено:", pricing.format_breakdown_lines(breakdown),
              "", texts.CONFIRM_QUESTION]

    await state.set_state(Order.confirm)
    await msg.answer("\n".join(lines), reply_markup=kb.order_confirm())


@router.callback_query(Order.confirm, F.data == "ord:edit")
async def cb_edit_menu(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    user = await repo.get_or_create_user(cb.from_user)
    many_pets = len(await repo.list_pets(user["id"])) > 1
    await cb.message.answer("Что поправить?", reply_markup=kb.order_edit(bool(data.get("outside_mkad")), many_pets))


@router.callback_query(Order.confirm, F.data == "ord:comment")
async def cb_comment(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Order.comment)
    await cb.message.answer(texts.ASK_COMMENT)


@router.message(Order.comment, F.text)
async def msg_comment(m: Message, state: FSMContext) -> None:
    await state.update_data(comment=m.text.strip()[:500])
    await m.answer(texts.COMMENT_SAVED)
    await show_confirm(m, state, m.from_user)


@router.callback_query(Order.confirm, F.data == "ord:back")
async def cb_edit_back(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await show_confirm(cb.message, state, cb.from_user)


@router.callback_query(Order.confirm, F.data == "ord:when")
async def cb_edit_when(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Order.when)
    await state.update_data(prefilled=True)  # после нового времени сразу возвращаемся на экран заказа
    await ask_when(cb.message, await _service_of(state))


@router.callback_query(Order.confirm, F.data == "ord:mkad")
async def cb_mkad(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    await state.update_data(outside_mkad=not data.get("outside_mkad"))
    await show_confirm(cb.message, state, cb.from_user)


@router.callback_query(Order.confirm, F.data == "ord:addr")
async def cb_edit_addr(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.set_state(Order.edit_address)
    await cb.message.answer("Напишите новый адрес для этого заказа или отправьте геопозицию.",
                            reply_markup=kb.address_ask())


@router.message(Order.edit_address, F.text)
async def msg_edit_addr(m: Message, state: FSMContext) -> None:
    await _address_input(m, state, m.text, "edit", Order.edit_address.state, m.from_user)


@router.callback_query(Order.confirm, F.data == "ord:pet")
async def cb_choose_pet(cb: CallbackQuery) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    await cb.message.answer("Для какого питомца?", reply_markup=kb.pick_pet(await repo.list_pets(user["id"])))


@router.callback_query(Order.confirm, F.data.startswith("pick:"))
async def cb_pick_pet(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    pet = await repo.get_pet(int(cb.data.split(":")[1]))
    if not pet or pet["user_id"] != user["id"]:  # чужого питомца выбрать нельзя
        return
    await state.update_data(pet_id=pet["id"])
    await show_confirm(cb.message, state, cb.from_user)


@router.callback_query(F.data == "ord:cancel")
async def cb_order_cancel(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer("Заказ не оформлен")
    await state.clear()
    await show_menu(cb.message, cb.from_user)


@router.callback_query(Order.confirm, F.data == "ord:ok")
async def cb_order_ok(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    data = await state.get_data()
    user = await repo.get_or_create_user(cb.from_user)
    start = from_db(data["start_at"])
    svc = await _service_of(state)
    grace = timedelta(minutes=15) if svc["category"] == "taxi" else timedelta(hours=23)  # час запаса на раздумья
    if start < to_local(now_utc()) + grace:  # пока клиент думал, время ушло
        await state.set_state(Order.when)
        await state.update_data(prefilled=True)
        await ask_when(cb.message, svc, "Это время уже не подходит. ")
        return
    total, breakdown, service, pet, params = await _price_for(data)
    order_id = await orders.create_order(
        {"user_id": user["id"], "pet_id": data["pet_id"], "service_id": service["id"],
         "start_at": start, "end_at": compute_end(service, start, data["quantity"]),
         "quantity": data["quantity"], "address": data["address"],
         "address_to": data.get("address_to"), "is_urgent": params["is_urgent"],
         "outside_mkad": params["outside_mkad"],
         "address_unverified": bool(data.get("unv_a") or data.get("unv_b")),
         "comment": data.get("comment"),
         "rider_name": data.get("rider_name"), "rider_phone": data.get("rider_phone")},
        total, breakdown, actor=f"client:{cb.from_user.id}",
    )
    await state.clear()
    await cb.message.answer(texts.ORDER_ACCEPTED.format(id=order_id) + "\n\nНужна ещё одна услуга?", reply_markup=kb.offer_order())
    o = await orders.get_order_full(order_id)
    await orders.notify_admins(
        cb.bot, f"🆕 Новая заявка\n\n{orders.order_card(o, for_staff=True)}",
        reply_markup=kb.admin_new_order(order_id, orders.chat_url(o["client_username"], o["client_tg_id"])))


# ---- Повтор последнего заказа ----

@router.callback_query(F.data == "menu:repeat")
async def cb_repeat(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    last = await repo.last_order(user["id"])
    if not last:
        await cb.message.answer("Пока нет заказов, которые можно повторить.")
        return
    service = await db.fetch_one("SELECT * FROM services WHERE id=? AND active=1", (last["service_id"],))
    pet = await repo.get_pet(last["pet_id"])
    if not service or not pet or not pet["is_active"]:
        await cb.message.answer("Эту услугу или питомца уже нельзя повторить. Оформите новый заказ.")
        await show_services(cb.message)
        return
    await state.clear()
    await state.update_data(service_id=service["id"], pet_id=pet["id"], quantity=int(last["quantity"]),
                            address=last["address"], address_to=last["address_to"],
                            outside_mkad=bool(last["outside_mkad"]), out_a=bool(last["outside_mkad"]),
                            out_b=False, unv_a=bool(last["address_unverified"]), unv_b=False,
                            prefilled=True)
    await state.set_state(Order.when)
    await ask_when(cb.message, service, f"Повторяем: {service['name']}, {last['address']}.\n")


# =====================================================================
# Мои заказы и отмена клиентом
# =====================================================================

@router.callback_query(F.data == "menu:orders")
async def cb_my_orders(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    user = await repo.get_or_create_user(cb.from_user)
    rows = await repo.user_orders(user["id"], 8)
    if not rows:
        await cb.message.answer("Заказов пока нет.", reply_markup=kb.back_to_menu())
        return
    cancellable = [r["id"] for r in rows if r["status"] in ("created", "confirmed")]
    active = [r["id"] for r in rows if r["status"] in orders.ACTIVE_STATUSES]
    await cb.message.answer(orders.format_my_orders(rows), reply_markup=kb.my_orders(cancellable, active))


class RequestOrder(StatesGroup):
    """Клиент нажал «Проблема» или «Перенос» и пишет текст заявки одним сообщением."""
    wait = State()


@router.callback_query(F.data.startswith("req:"))
async def cb_request_start(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    _, kind, oid_s = cb.data.split(":")
    oid = int(oid_s)
    user = await repo.get_or_create_user(cb.from_user)
    o = await orders.get_order_full(oid)
    if not o or o["user_id"] != user["id"]:  # чужие заказы не трогаем
        return
    await state.set_state(RequestOrder.wait)
    await state.update_data(kind=kind, order_id=oid)
    if kind == "problem":
        ask = "Опишите, пожалуйста, что случилось"
    else:
        ask = "Напишите, на какое время перенести заказ"
    await cb.message.answer(f"{ask} по заказу №{oid}. Одним сообщением.")


@router.message(StateFilter(RequestOrder.wait), F.text)
async def msg_request_text(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    user = await repo.get_or_create_user(msg.from_user)
    o = await orders.get_order_full(data["order_id"])
    if not o or o["user_id"] != user["id"]:
        await msg.answer("Заказ не найден.", reply_markup=kb.back_to_menu())
        return
    ok, text = await orders.client_request(msg.bot, data["order_id"], data["kind"], msg.text,
                                           f"client:{msg.from_user.id}")
    await msg.answer(text, reply_markup=kb.back_to_menu())


@router.callback_query(F.data.startswith("cancel:"))
async def cb_cancel_ask(cb: CallbackQuery) -> None:
    await cb.answer()
    order_id = int(cb.data.split(":")[1])
    await cb.message.answer(f"Отменить заказ №{order_id}?", reply_markup=kb.cancel_confirm(order_id))


@router.callback_query(F.data.startswith("cancel_yes:"))
async def cb_cancel_yes(cb: CallbackQuery) -> None:
    await cb.answer()
    order_id = int(cb.data.split(":")[1])
    user = await repo.get_or_create_user(cb.from_user)
    o = await orders.get_order_full(order_id)
    if not o or o["user_id"] != user["id"]:  # чужие заказы отменять нельзя
        return
    if o["status"] not in ("created", "confirmed"):
        await cb.message.answer("Этот заказ уже нельзя отменить самостоятельно — напишите нам.")
        return
    ok, msg_text = await orders.change_status(cb.bot, order_id, "cancelled",
                                              f"client:{cb.from_user.id}", "отменён клиентом")
    if not ok:
        await cb.message.answer(msg_text)


# =====================================================================
# Оплата: дежурные реквизиты и «я оплатил»
# =====================================================================

async def _claim_paid(bot, order: dict) -> None:
    await orders.notify_admins(
        bot, f"💰 Клиент {order['client_name'] or '—'} пишет, что оплатил заказ №{order['id']} "
             f"({rub(order['price_total'])}). Проверьте поступление.",
        reply_markup=kb.admin_paid_check(order["id"], orders.chat_url(order["client_username"], order["client_tg_id"])))


@router.callback_query(F.data.startswith("pay:"))
async def cb_pay(cb: CallbackQuery) -> None:
    await cb.answer()
    try:
        _, kind, oid = cb.data.split(":")
        order_id = int(oid)
    except ValueError:
        await cb.message.answer(texts.STALE)
        return
    user = await repo.get_or_create_user(cb.from_user)
    o = await orders.get_order_full(order_id)
    if not o or o["user_id"] != user["id"] or o["status"] != "confirmed":
        await cb.message.answer(texts.PAY_CLAIM_NONE)
        return
    if kind in ("sbp", "card") and payinfo.available(os.environ, kind):
        make = payinfo.sbp_text if kind == "sbp" else payinfo.card_text
        await cb.message.answer(make(os.environ, order_id, o["price_total"]))
    elif kind == "claim":
        await _claim_paid(cb.bot, o)
        await cb.message.answer(texts.PAY_CLAIM_THANKS)
    else:
        await cb.message.answer(texts.PAY_CLAIM_NONE)


@router.message(StateFilter(None), F.text.func(texts.is_paid_claim))
async def msg_paid_claim(m: Message) -> None:
    """Клиент просто пишет «оплатил» — передаём администратору по его ожидающим оплаты заказам."""
    user = await repo.get_or_create_user(m.from_user)
    rows = await db.fetch_all("SELECT id FROM orders WHERE user_id=? AND status='confirmed'", (user["id"],))
    if not rows:
        await m.answer(texts.PAY_CLAIM_NONE)
        return
    for r in rows:
        await _claim_paid(m.bot, await orders.get_order_full(r["id"]))
    await m.answer(texts.PAY_CLAIM_THANKS)


# =====================================================================
# 9. Отзывы
# =====================================================================

@router.callback_query(F.data.startswith("rev:"))
async def cb_rating(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    _, order_id, rating = cb.data.split(":")
    order_id, rating = int(order_id), int(rating)
    user = await repo.get_or_create_user(cb.from_user)
    o = await orders.get_order_full(order_id)
    if not o or o["user_id"] != user["id"] or o["status"] != "completed":
        return
    if await db.fetch_one("SELECT 1 FROM reviews WHERE order_id=?", (order_id,)):
        await cb.message.answer("Отзыв по этому заказу уже есть, спасибо!")
        return
    await state.set_state(Review.text)
    await state.update_data(review_order=order_id, review_rating=rating)
    await cb.message.answer(texts.REVIEW_TEXT, reply_markup=kb.skip_review())


async def _save_review(msg: Message, tg_user, state: FSMContext, text: str | None) -> None:
    data = await state.get_data()
    user = await repo.get_or_create_user(tg_user)
    o = await orders.get_order_full(data["review_order"])
    await state.clear()
    try:
        await db.execute(
            "INSERT INTO reviews (order_id, user_id, executor_id, rating, text) VALUES (?,?,?,?,?)",
            (o["id"], user["id"], o["executor_id"], data["review_rating"], text))
    except Exception:  # UNIQUE(order_id): повторная отправка
        log.warning("Отзыв по заказу %s уже существует", o["id"])
        return
    await msg.answer(texts.REVIEW_THANKS)
    if data["review_rating"] <= 3:  # плохую оценку админ должен увидеть сразу
        await orders.notify_admins(msg.bot, f"⚠️ Низкая оценка {data['review_rating']}/5 по заказу №{o['id']} "
                                            f"({o['executor_name']}): {text or 'без текста'}")


@router.message(Review.text, F.text)
async def msg_review(m: Message, state: FSMContext) -> None:
    await _save_review(m, m.from_user, state, m.text.strip()[:1000])


@router.callback_query(Review.text, F.data == "rev_skip")
async def cb_review_skip(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await _save_review(cb.message, cb.from_user, state, None)


# =====================================================================
# Запасные обработчики (должны быть последними в роутере)
# =====================================================================

STATE_HINTS = {
    Profile.pet_species.state: texts.HINT_SPECIES,
    Profile.pet_size.state: texts.HINT_SIZE,
    Profile.pet_traits.state: texts.HINT_TRAITS,
    Profile.phone.state: texts.HINT_PHONE,
    Addr.pick.state: texts.HINT_ADDR_PICK,
    Profile.recognize.state: texts.HINT_RECOGNIZE,
    Order.taxi_pet.state: "Выберите питомца кнопкой ниже.",
    Order.taxi_warn.state: "Это обязательное условие. Прочитайте и нажмите «Понял(а), возьму».",
    Order.taxi_for.state: "Выберите кнопкой: для вас или для другого человека.",
}


@router.message()
async def fallback_any(m: Message, state: FSMContext) -> None:
    """Всё, что не подошло (текст не в тот момент, стикер, фото, голос): говорим, что ждём."""
    cur = await state.get_state()
    if cur:
        await m.answer(STATE_HINTS.get(cur, texts.FINISH_QUESTION))
    elif m.text and not m.text.startswith("/") and not is_admin(m.from_user):
        # вопрос вне сценария: админу (и в портал) или собеседнику в открытом чате по заказу
        if await talk.incoming(m.bot, m.from_user.id, m.text) == "admin":
            await m.answer(texts.SENT_TO_ADMIN, reply_markup=kb.offer_order())
    elif m.text:
        await show_menu(m, m.from_user)
    elif m.web_app_data or m.location or m.contact:  # адрес, место или номер пришли вне шага — не молчим
        await m.answer(texts.LOST_STEP)
        await show_menu(m, m.from_user)
    # стикеры и файлы вне диалога молча игнорируем


@router.callback_query()
async def fallback_callback(cb: CallbackQuery) -> None:
    await cb.answer(texts.STALE, show_alert=True)
