"""Клиентская часть: согласие ПДн, профиль, заказ (≤ 4 экрана), мои заказы, повтор, отзывы."""
import logging
from datetime import timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message, ReplyKeyboardRemove

import breeds
import db
import geo
import keyboards as kb
import orders
import pricing
import repo
import texts
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
    pet_notes = State()


class Order(StatesGroup):
    when = State()
    qty = State()
    point_a = State()
    point_b = State()
    confirm = State()
    edit_address = State()


class Addr(StatesGroup):
    pick = State()  # клиент выбирает адрес из найденных на карте


class Review(StatesGroup):
    text = State()


# =====================================================================
# 1. Согласие на обработку ПДн, главное меню
# =====================================================================

async def show_consent(msg: Message) -> None:
    await msg.answer(texts.CONSENT_TEXT, reply_markup=kb.consent(config.policy_url))


async def show_menu(msg: Message, tg_user, text: str = texts.WELCOME) -> None:
    user = await repo.get_or_create_user(tg_user)
    has_orders = await repo.last_order(user["id"]) is not None
    await msg.answer(text, reply_markup=kb.main_menu(has_orders))


@router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext) -> None:
    await state.clear()
    user = await repo.get_or_create_user(m.from_user)
    if not user["pdn_consent"]:
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
    lines.append("")
    lines.append("Рассылки: " + ("включены ✅" if user["marketing_consent"] else "выключены"))
    await msg.answer("\n".join(lines), reply_markup=kb.profile(bool(user["marketing_consent"])))


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
    await show_profile(cb.message, cb.from_user)


async def start_profile(msg: Message, state: FSMContext, after: str | None, add_pet_only: bool = False) -> None:
    """Запускает опрос. after='order' — потом сразу к выбору услуги."""
    await state.clear()
    await state.update_data(after=after)
    if add_pet_only:
        await state.set_state(Profile.pet_name)
        await msg.answer(texts.ASK_PET_NAME)
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


@router.message(Profile.name, F.text)
async def p_name(m: Message, state: FSMContext) -> None:
    name = breeds.check_person_name(m.text)
    if not name:
        await m.answer(texts.BAD_PERSON_NAME)
        return
    await state.update_data(name=name)
    await state.set_state(Profile.phone)
    await m.answer(texts.ASK_PHONE, reply_markup=kb.phone_request())


@router.message(Profile.phone, F.contact)
async def p_phone_contact(m: Message, state: FSMContext) -> None:
    await _save_phone(m, state, m.contact.phone_number)


@router.message(Profile.phone, F.text)
async def p_phone_text(m: Message, state: FSMContext) -> None:
    await _save_phone(m, state, m.text)


async def _save_phone(m: Message, state: FSMContext, raw: str) -> None:
    phone = normalize_phone(raw)
    if not phone:
        await m.answer(texts.BAD_PHONE)
        return
    await state.update_data(phone=phone)
    await state.set_state(Profile.address)
    await m.answer(texts.ASK_ADDRESS + texts.MAP_HINT * bool(config.webapp_url), reply_markup=kb.address_ask())


# =====================================================================
# Проверка адреса по карте: клиент пишет адрес → бот показывает найденные варианты кнопками
# =====================================================================

ADDR_STATES = {Profile.address.state: "profile", Order.point_a.state: "a",
               Order.point_b.state: "b", Order.edit_address.state: "edit"}


@router.message(Profile.address, F.text)
async def p_address(m: Message, state: FSMContext) -> None:
    await _address_input(m, state, m.text, "profile", Profile.address.state, m.from_user)


async def _address_input(m: Message, state: FSMContext, text: str, target: str, back: str, tg_user) -> None:
    text = " ".join(text.split())
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
             "outside": p.outside_mkad, "has_house": p.has_house} for p in places]
    await state.update_data(addr_target=target, addr_back=back, addr_text=text, addr_options=opts,
                            addr_fails=fails if opts else fails + 1)
    await state.set_state(Addr.pick)
    if opts:
        await m.answer(texts.ADDR_PICK, reply_markup=kb.address_options([o["label"] for o in opts], False))
    else:
        fails += 1
        await m.answer(texts.ADDR_NOT_FOUND if fails < 2 else texts.ADDR_NOT_FOUND_KEEP,
                       reply_markup=kb.address_options([], can_keep=fails >= 2))


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
        await cb.message.answer("Напишите адрес ещё раз.", reply_markup=kb.address_ask())
        return
    if arg == "keep":
        if data.get("addr_fails", 0) < 2 or not data.get("addr_text"):
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
        await cb.message.answer(texts.ADDR_NO_HOUSE, reply_markup=kb.address_ask())
        return
    await _address_done(cb.message, state, data["addr_target"], o["label"][:200], o["district"],
                        o["outside"], True, cb.from_user)


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
    opts = [{"label": place.label, "district": place.district, "outside": place.outside_mkad, "has_house": True}]
    await state.update_data(addr_target=target, addr_back=back, addr_text=place.label, addr_options=opts,
                            addr_fails=0)
    await state.set_state(Addr.pick)
    await m.answer(texts.ADDR_PICK, reply_markup=kb.address_options([place.label], False))


@router.message(StateFilter(Profile.address, Order.point_a, Order.point_b, Order.edit_address, Addr.pick),
                F.web_app_data)
async def addr_webapp(m: Message, state: FSMContext) -> None:
    """Адрес выбран в мини-приложении с картой. Координаты и текст перепроверяем, как и любой другой ввод."""
    cur = await state.get_state()
    data = await state.get_data()
    target = ADDR_STATES.get(cur) or data.get("addr_target")
    pick = geo.parse_pick(m.web_app_data.data)
    if not pick:
        await m.answer(texts.ADDR_WEB_BAD, reply_markup=kb.address_ask())
        return
    place = await geo.reverse(pick["lat"], pick["lon"])  # район и МКАД; если карта молчит — по координатам
    outside = place.outside_mkad if place else geo.outside_by_coords(pick["lat"], pick["lon"])
    await _address_done(m, state, target, pick["label"][:200], place.district if place else None, outside,
                        True, m.from_user)


async def _address_done(msg: Message, state: FSMContext, target: str, label: str, district: str | None,
                        outside: bool | None, verified: bool, tg_user) -> None:
    """Адрес принят (проверен картой или оставлен как есть). Идём дальше по сценарию."""
    await state.update_data(addr_fails=0, addr_options=[])
    if target == "profile":
        data = await state.get_data()
        user = await repo.get_or_create_user(tg_user)
        fields = {"name": data["name"], "phone": data["phone"], "address": label,
                  "address_verified": int(verified), "outside_mkad": int(bool(outside))}
        if district:
            fields["district"] = district
        await repo.update_user(user["id"], **fields)
        if await repo.list_pets(user["id"]):
            await finish_profile(msg, state, tg_user)
        else:
            await state.set_state(Profile.pet_name)
            await msg.answer(texts.ASK_PET_NAME, reply_markup=ReplyKeyboardRemove())
        return
    if target in ("a", "edit"):
        await state.update_data(address=label, out_a=bool(outside), unv_a=not verified)
    else:
        await state.update_data(address_to=label, out_b=bool(outside), unv_b=not verified)
    data = await state.get_data()
    await state.update_data(outside_mkad=bool(data.get("out_a")) or bool(data.get("out_b")))
    if target == "a":
        await state.set_state(Order.point_b)
        await msg.answer(texts.ASK_POINT_B, reply_markup=kb.address_ask())
    else:
        await show_confirm(msg, state, tg_user)


@router.message(Profile.pet_name, F.text)
async def p_pet_name(m: Message, state: FSMContext) -> None:
    name = breeds.check_pet_name(m.text)
    if not name:
        await m.answer(texts.BAD_PET_NAME)
        return
    await state.update_data(pet_name=name)
    await state.set_state(Profile.pet_species)
    await m.answer(texts.ASK_PET_SPECIES, reply_markup=kb.species())


@router.callback_query(Profile.pet_species, F.data.startswith("sp:"))
async def p_pet_species(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    species = cb.data.split(":", 1)[1]
    await state.update_data(pet_species=species, pet_breed=None, breed_unverified=False)
    await state.set_state(Profile.pet_breed)
    await cb.message.answer(texts.ASK_PET_OTHER if species == "другое" else texts.ASK_PET_BREED)


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
    await state.update_data(pet_size=size)
    await state.set_state(Profile.pet_notes)
    await msg.answer(texts.ASK_PET_NOTES, reply_markup=kb.no_notes())


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
    await repo.add_pet(user["id"], data["pet_name"], data["pet_species"], data.get("pet_breed"),
                       data["pet_size"], notes.strip() if notes else None)
    await finish_profile(msg, state, tg_user)


async def finish_profile(msg: Message, state: FSMContext, tg_user=None) -> None:
    tg_user = tg_user or msg.from_user
    after = (await state.get_data()).get("after")
    await state.clear()
    if after == "order":
        await show_services(msg)
    else:
        await msg.answer("Сохранила ✅")
        await show_profile(msg, tg_user)


# =====================================================================
# 3. Сценарий заказа: услуга → дата/длительность → подтверждение (+ цена)
# =====================================================================

async def show_services(msg: Message) -> None:
    items = await db.fetch_all("SELECT * FROM services WHERE active=1 ORDER BY id")
    await msg.answer(texts.CHOOSE_SERVICE, reply_markup=kb.services(items))


@router.callback_query(F.data == "menu:order")
async def cb_order(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    user = await repo.get_or_create_user(cb.from_user)
    # при первом заказе собираем профиль и питомца, потом возвращаемся к выбору услуги
    if not repo.profile_complete(user) or not await repo.list_pets(user["id"]):
        await start_profile(cb.message, state, after="order")
        return
    await state.clear()
    await show_services(cb.message)


@router.callback_query(F.data.startswith("svc:"))
async def cb_service(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    service = await db.fetch_one("SELECT * FROM services WHERE id=? AND active=1",
                                 (int(cb.data.split(":")[1]),))
    if not service:
        await cb.message.answer("Эта услуга сейчас недоступна.")
        return
    user = await repo.get_or_create_user(cb.from_user)
    pets = await repo.list_pets(user["id"])
    if not pets or not repo.profile_complete(user):
        await start_profile(cb.message, state, after="order")
        return
    await state.clear()
    await state.update_data(service_id=service["id"], pet_id=pets[0]["id"], quantity=1,
                            address=user["address"], address_to=None,
                            outside_mkad=bool(user["outside_mkad"]), out_a=bool(user["outside_mkad"]),
                            out_b=False, unv_a=not user["address_verified"], unv_b=False, prefilled=False)
    await state.set_state(Order.when)
    ask = texts.ASK_WHEN_START if service["unit"] == "сутки" else texts.ASK_WHEN
    await cb.message.answer(ask)


@router.message(Order.when, F.text)
async def got_when(m: Message, state: FSMContext) -> None:
    dt = parse_user_datetime(m.text)
    if dt is None:
        await m.answer(texts.BAD_WHEN)
        return
    if dt < to_local(now_utc()) + timedelta(minutes=30):
        await m.answer(texts.PAST_WHEN)
        return
    if dt > to_local(now_utc()) + timedelta(days=366):
        await m.answer(texts.FAR_WHEN)
        return
    data = await state.get_data()
    service = await db.fetch_one("SELECT * FROM services WHERE id=?", (data["service_id"],))
    await state.update_data(start_at=to_db(dt))

    if data.get("prefilled"):  # «повторить заказ»: остальное уже известно
        await show_confirm(m, state, m.from_user)
    elif service["unit"] == "час":
        await state.set_state(Order.qty)
        await m.answer(texts.ASK_HOURS, reply_markup=kb.quantity([1, 2, 3, 4, 6, 8]))
    elif service["unit"] == "сутки":
        await state.set_state(Order.qty)
        await m.answer(texts.ASK_NIGHTS, reply_markup=kb.quantity([1, 2, 3, 5, 7, 10, 14]))
    elif service["category"] == "taxi":
        user = await repo.get_or_create_user(m.from_user)
        await state.set_state(Order.point_a)
        await m.answer(texts.ASK_POINT_A, reply_markup=kb.use_profile_address(user["address"]))
        await m.answer("Или напишите другой адрес, или отправьте геопозицию.", reply_markup=kb.address_ask())
    else:
        await show_confirm(m, state, m.from_user)


async def _set_qty(msg: Message, tg_user, state: FSMContext, raw: str) -> None:
    data = await state.get_data()
    service = await db.fetch_one("SELECT * FROM services WHERE id=?", (data["service_id"],))
    limit = 12 if service["unit"] == "час" else 30
    if not raw.isdigit() or not 1 <= int(raw) <= limit:
        await msg.answer(f"{texts.BAD_QTY} (от 1 до {limit})")
        return
    await state.update_data(quantity=int(raw))
    await show_confirm(msg, state, tg_user)


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
                            unv_a=not user["address_verified"])
    data = await state.get_data()
    await state.update_data(outside_mkad=bool(data.get("out_a")) or bool(data.get("out_b")))
    await state.set_state(Order.point_b)
    await cb.message.answer(texts.ASK_POINT_B, reply_markup=kb.address_ask())


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
    }
    total, breakdown = await pricing.calculate_price(service["id"], params)
    return total, breakdown, service, pet, params


async def show_confirm(msg: Message, state: FSMContext, tg_user) -> None:
    data = await state.get_data()
    total, breakdown, service, pet, _ = await _price_for(data)
    user = await repo.get_or_create_user(tg_user)
    pets = await repo.list_pets(user["id"])
    start = from_db(data["start_at"])
    end = compute_end(service, start, data["quantity"])

    when = fmt_local(start) + (f" → {fmt_local(end)}" if end and service["unit"] in ("час", "сутки") else "")
    lines = ["Проверьте заказ:", "", f"Услуга: {service['name']}", f"Когда: {when}"]
    if data.get("address_to"):
        lines += [f"Откуда: {data['address']}", f"Куда: {data['address_to']}"]
    else:
        lines.append(f"Адрес: {data['address']}")
    if pet:
        size = f", {pet['size']}" if pet["size"] else ""
        lines.append(f"Питомец: {pet['name']} ({pet['species']}{size})")
    lines += ["", "💰 Расчёт:", pricing.format_breakdown(breakdown, total)]

    await state.set_state(Order.confirm)
    await msg.answer("\n".join(lines),
                     reply_markup=kb.order_confirm(bool(data.get("outside_mkad")), len(pets) > 1))


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
    if start < to_local(now_utc()) + timedelta(minutes=15):  # пока клиент думал, время ушло
        await state.set_state(Order.when)
        await state.update_data(prefilled=True)
        await cb.message.answer("Это время уже подходит к концу. " + texts.ASK_WHEN)
        return
    total, breakdown, service, pet, params = await _price_for(data)
    order_id = await orders.create_order(
        {"user_id": user["id"], "pet_id": data["pet_id"], "service_id": service["id"],
         "start_at": start, "end_at": compute_end(service, start, data["quantity"]),
         "quantity": data["quantity"], "address": data["address"],
         "address_to": data.get("address_to"), "is_urgent": params["is_urgent"],
         "outside_mkad": params["outside_mkad"],
         "address_unverified": bool(data.get("unv_a") or data.get("unv_b"))},
        total, breakdown, actor=f"client:{cb.from_user.id}",
    )
    await state.clear()
    await cb.message.answer(texts.ORDER_ACCEPTED.format(id=order_id), reply_markup=kb.back_to_menu())
    o = await orders.get_order_full(order_id)
    await orders.notify_admins(cb.bot, f"🆕 Новая заявка\n\n{orders.order_card(o, for_staff=True)}\n\n"
                                       f"Подтвердить: /confirm {order_id}")


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
    await cb.message.answer(f"Повторяем: {service['name']}, {last['address']}.\n" + texts.ASK_WHEN)


# =====================================================================
# Мои заказы и отмена клиентом
# =====================================================================

@router.callback_query(F.data == "menu:orders")
async def cb_my_orders(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    user = await repo.get_or_create_user(cb.from_user)
    rows = await db.fetch_all(
        """SELECT o.id, o.status, o.start_at, o.price_total, s.name FROM orders o
           JOIN services s ON s.id=o.service_id WHERE o.user_id=? ORDER BY o.id DESC LIMIT 5""",
        (user["id"],))
    if not rows:
        await cb.message.answer("Заказов пока нет.", reply_markup=kb.back_to_menu())
        return
    lines = ["📋 Ваши последние заказы:", ""]
    for r in rows:
        lines.append(f"№{r['id']} · {r['name']} · {fmt_local(r['start_at'])} · "
                     f"{rub(r['price_total'])} · {texts.STATUS_LABELS[r['status']]}")
    lines.append("\nОтменить можно заказ до оплаты. После оплаты — напишите нам.")
    cancellable = [r["id"] for r in rows if r["status"] in ("created", "confirmed")]
    await cb.message.answer("\n".join(lines), reply_markup=kb.my_orders(cancellable))


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
    Profile.phone.state: texts.HINT_PHONE,
    Addr.pick.state: texts.HINT_ADDR_PICK,
}


@router.message()
async def fallback_any(m: Message, state: FSMContext) -> None:
    """Всё, что не подошло (текст не в тот момент, стикер, фото, голос): говорим, что ждём."""
    cur = await state.get_state()
    if cur:
        await m.answer(STATE_HINTS.get(cur, texts.FINISH_QUESTION))
    elif m.text:  # стикеры и файлы вне диалога молча игнорируем
        await show_menu(m, m.from_user)


@router.callback_query()
async def fallback_callback(cb: CallbackQuery) -> None:
    await cb.answer(texts.STALE, show_alert=True)
