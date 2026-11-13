"""Бизнес-логика заказов: создание, машина статусов, оплата, назначение исполнителя, уведомления.

Статусы:  created → confirmed → paid → assigned → in_progress → completed
          (из любого незавершённого можно уйти в cancelled)
Платёж:   pending → link_sent → paid → refunded
"""
import asyncio
import os
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

import db
import payinfo
import pricing
import keyboards as kb
import texts
import traits
from config import config
from utils import fmt_local, from_db, min_age, now_utc, rub, to_db

log = logging.getLogger(__name__)

TRANSITIONS = {
    "created": {"confirmed", "cancelled"},
    "confirmed": {"paid", "cancelled"},
    "paid": {"assigned", "cancelled"},
    "assigned": {"in_progress", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}
ACTIVE_STATUSES = ("created", "confirmed", "paid", "assigned", "in_progress")


class StatusConflict(Exception):
    """Статус заказа уже изменился (например, двумя админами одновременно)."""


# ---------- Чтение ----------

async def get_order_full(order_id: int) -> dict | None:
    """Заказ вместе с услугой, клиентом, питомцем и исполнителем."""
    return await db.fetch_one(
        """SELECT o.*, s.name AS service_name, s.category, s.unit,
                  u.tg_id AS client_tg_id, u.name AS client_name, u.phone AS client_phone,
                  u.tg_username AS client_username, u.contact_pref AS client_contact_pref,
                  p.name AS pet_name, p.species AS pet_species, p.breed AS pet_breed,
                  p.size AS pet_size, p.notes AS pet_notes, p.traits AS pet_traits,
                  e.name AS executor_name, e.tg_id AS executor_tg_id, e.phone AS executor_phone
           FROM orders o
           JOIN services s ON s.id = o.service_id
           JOIN users u ON u.id = o.user_id
           LEFT JOIN pets p ON p.id = o.pet_id
           LEFT JOIN executors e ON e.id = o.executor_id
           WHERE o.id = ?""",
        (order_id,),
    )


def price_explanation(breakdown: list, total: int) -> str:
    """Разбивка цены заказа для администратора: из чего сложилась сумма."""
    from pricing import format_breakdown
    return "Расчёт цены:\n" + format_breakdown(breakdown or [], total)


def price_note(breakdown: list, total: int) -> str:
    """Одна строка для журнала заказа: формула и итог на момент создания."""
    from utils import rub
    parts = "; ".join(f"{name} {rub(amount)}" for name, amount in breakdown or [])
    return f"Расчёт при создании: {parts} = {rub(total)}"


def order_card(o: dict, *, for_staff: bool = False) -> str:
    """Текстовая карточка заказа. for_staff=True добавляет контакты клиента и заметки о питомце."""
    lines = [f"Заказ №{o['id']} · {texts.STATUS_LABELS[o['status']]}"]
    if for_staff and o.get("created_at"):  # когда заказ появился: сверху, чтобы было видно сразу
        lines.append(f"Зарегистрирован: {fmt_local(o['created_at'])}")
    lines.append(f"Услуга: {o['service_name']}")
    when = fmt_local(o["start_at"])
    if o["end_at"] and o["unit"] in ("час", "сутки"):
        when += f" → {fmt_local(o['end_at'])}"
    lines.append(f"Когда: {when}")
    if o["address_to"]:
        lines.append(f"Подача: {o['address']}\nКуда: {o['address_to']}")
    else:
        lines.append(f"Адрес: {o['address']}")
    if o["outside_mkad"]:
        lines.append("За МКАД: да")
    if o.get("rider_name"):
        lines.append(f"Поедет: {o['rider_name']}, {o['rider_phone']}")
    if o.get("comment"):
        lines.append(f"Комментарий: {o['comment']}")
    if for_staff and o.get("address_unverified"):
        lines.append("⚠️ Адрес не подтверждён картой — уточните у клиента")
    if o["pet_name"]:
        size = f", {o['pet_size']}" if o["pet_size"] else ""
        pet = f"Питомец: {o['pet_name']} ({o['pet_species']}{size})"
        if o["pet_breed"]:
            pet += f", {o['pet_breed']}"
        lines.append(pet)
        if for_staff:
            codes = traits.unpack(o.get("pet_traits"))
            if codes:
                warn = "⚠️ " if traits.has_danger(codes) else ""
                lines.append(f"{warn}Повадки: {traits.describe(codes)}")
            if o["pet_notes"]:
                lines.append(f"Здоровье и особенности: {o['pet_notes']}")
    if for_staff:
        lines.append(f"Клиент: {o['client_name']}, {o['client_phone']}")
    if for_staff:
        lines.append(f"Вилка для клиента: {pricing.format_range_short(o['price_total'])}")
        lines.append(f"Сумма (расчёт): {rub(o['price_total'])}")
    else:
        lines.append(f"Стоимость: {pricing.format_range_short(o['price_total'])}")
    if for_staff:
        breakdown = o.get("price_breakdown")
        if isinstance(breakdown, str):
            breakdown = json.loads(breakdown) if breakdown else []
        if breakdown:
            lines.append(price_explanation(breakdown, o["price_total"]))
    return "\n".join(lines)


def format_my_orders(rows: list[dict]) -> str:
    """Экран «Мои заказы»: сверху заказы в работе (подсвечены 🟢), ниже отдельным блоком завершённые и отменённые."""
    active = [r for r in rows if r["status"] in ACTIVE_STATUSES]
    done = [r for r in rows if r["status"] not in ACTIVE_STATUSES]

    def line(r, mark):
        return (f"{mark} №{r['id']} · {r['name']} · {fmt_local(r['start_at'])} · {pricing.format_range_short(r['price_total'])}\n"
                f"      {texts.STATUS_LABELS[r['status']]}")
    out = ["📋 Ваши заказы"]
    if active:
        out += ["", "🟢 В работе:"] + [line(r, "🟢") for r in active]
    if done:
        out += ["", "Завершённые и отменённые:"] + [line(r, "▫️") for r in done]
    out.append("\nОтменить можно заказ до оплаты. После оплаты — напишите нам.")
    return "\n".join(out)


# ---------- Уведомления ----------

async def notify(bot: Bot, tg_id: int, text: str, reply_markup=None) -> bool:
    """Отправка с защитой: если пользователь заблокировал бота — помечаем и идём дальше."""
    try:
        await bot.send_message(tg_id, text, reply_markup=reply_markup)
        return True
    except TelegramForbiddenError:
        await db.execute(f"UPDATE users SET is_blocked=1, {db.touch()} WHERE tg_id=?", (tg_id,))
    except Exception:
        log.exception("Не удалось отправить сообщение %s", tg_id)
    return False


def chat_url(username: str | None, tg_id: int) -> str:
    """Ссылка «открыть чат с клиентом»: по username, а без него — по Telegram ID."""
    if tg_id < 0:  # клиент из браузера: чат идёт через бота, кнопка превращается в «ответить» (wc:)
        return f"web:{tg_id}"
    return f"https://t.me/{username}" if username else f"tg://user?id={tg_id}"


async def pending_payment_orders() -> list[dict]:
    """Подтверждённые заказы, по которым ещё ждём оплату (сюда админ присылает ссылку)."""
    return await db.fetch_all(
        """SELECT o.id, o.status, o.price_total, u.name AS client_name FROM orders o
           JOIN users u ON u.id=o.user_id JOIN payments p ON p.order_id=o.id
           WHERE o.status='confirmed' AND p.status IN ('pending','link_sent') ORDER BY o.id""")


async def notify_admins(bot: Bot, text: str, reply_markup=None, copy_helper: bool = True) -> None:
    if not config.admin_ids:
        log.error("ADMIN_IDS не задан: некому отправить уведомление")
    if copy_helper:
        _bg(_helper_copy(text))  # копия в бот «Мой помощник»: в фоне, один раз на уведомление
    for admin_id in config.admin_ids:
        await _push_admin(admin_id, text)  # пуш на телефон — первым и независимо от Telegram
        _bg(_mail_admin(admin_id, text))    # копия письмом: в фоне, чтобы не задерживать ответ клиенту
        if not await notify(bot, admin_id, text, reply_markup):
            log.error("Не удалось отправить уведомление админу %s (он нажал /start у бота?)", admin_id)


_tasks: set = set()


def _bg(coro) -> None:
    t = asyncio.ensure_future(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


async def _helper_post(url: str, text: str, key: str) -> int:
    import aiohttp
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
        async with s.post(url, json={"text": text[:3800]}, headers={"X-Site-Key": key}) as r:
            return r.status


async def _helper_copy(text: str) -> None:
    """Копия уведомления в «Мой помощник» (HELPER_NOTIFY_URL + тот же SITE_LEADS_KEY). Нет переменных или не https — выключено; сбой тихий."""
    url, key = (os.getenv("HELPER_NOTIFY_URL") or "").strip(), os.getenv("SITE_LEADS_KEY") or ""
    if not url.startswith("https://") or len(key) < 24:
        return
    try:
        code = await _helper_post(url, text, key)
        if code != 200:
            log.warning("Копия в «Мой помощник»: ответ %s", code)
    except Exception as e:  # noqa: BLE001
        log.warning("Копия в «Мой помощник» не ушла (%s)", type(e).__name__)


async def _mail_admin(admin_id: int, text: str) -> None:
    """Копия уведомления письмом на почту админа (ADMIN_EMAIL или адрес из «Вход и почта»). Нужны SMTP_* в защищённых переменных хостинга."""
    import mailer
    import portal
    try:
        if not mailer.configured():
            return
        to = await portal.admin_email(admin_id)
        if not to:
            return
        first = (text.strip().splitlines() or ["Уведомление"])[0][:90]
        ok, why = await mailer.send_checked(to, f"Зооконсьерж: {first}", text)
        if not ok:
            log.error("Письмо админу не отправлено: %s", why)
    except Exception:
        log.exception("Письмо админу не отправлено")


async def _push_admin(admin_id: int, text: str) -> None:
    """Пуш на телефон админа (Web Push): сбой не мешает Telegram."""
    import webpush
    try:
        await webpush.notify(admin_id, text, title="🔔 Зооконсьерж: для вас")
    except Exception:
        log.exception("Пуш админу %s не отправлен", admin_id)


# ---------- Создание заказа ----------

async def create_order(data: dict, total: int, breakdown: list, actor: str) -> int:
    """data: user_id, pet_id, service_id, start_at(datetime), end_at(datetime|None), quantity,
    address, address_to, is_urgent, outside_mkad. Цена и разбивка фиксируются в заказе."""
    params = {"quantity": data["quantity"], "is_urgent": data["is_urgent"]}
    async with db.transaction() as conn:
        cur = await conn.execute(
            """INSERT INTO orders (user_id, pet_id, service_id, status, start_at, end_at, quantity,
                                   address, address_to, is_urgent, outside_mkad, params_json,
                                   price_total, price_breakdown, address_unverified, comment, rider_name, rider_phone)
               VALUES (?,?,?,'created',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (data["user_id"], data["pet_id"], data["service_id"], to_db(data["start_at"]),
             to_db(data["end_at"]) if data.get("end_at") else None, data["quantity"],
             data["address"], data.get("address_to"), int(data["is_urgent"]),
             int(data["outside_mkad"]), json.dumps(params, ensure_ascii=False), total,
             json.dumps(breakdown, ensure_ascii=False), int(data.get("address_unverified", False)),
             (data.get("comment") or None), data.get("rider_name"), data.get("rider_phone")),
        )
        order_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO order_statuses_log (order_id, from_status, to_status, actor, note) VALUES (?,NULL,'created',?,?)",
            (order_id, actor, price_note(breakdown, total)),
        )
        await conn.execute("INSERT INTO payments (order_id, amount) VALUES (?,?)", (order_id, total))
    return order_id


# ---------- Машина статусов ----------

async def _transition(conn, order_id: int, old: str, new: str, actor: str,
                      extra_set: str = "", extra_params: tuple = ()) -> None:
    """Меняет статус только если он всё ещё `old` (защита от гонок) и пишет журнал."""
    extra = f"{extra_set}, " if extra_set else ""
    cur = await conn.execute(
        f"UPDATE orders SET status=?, {extra}updated_at=datetime('now') WHERE id=? AND status=?",
        (new, *extra_params, order_id, old),
    )
    if cur.rowcount != 1:
        raise StatusConflict
    await conn.execute(
        "INSERT INTO order_statuses_log (order_id, from_status, to_status, actor) VALUES (?,?,?,?)",
        (order_id, old, new, actor),
    )
    # побочные эффекты в БД — в той же транзакции
    if new == "paid":
        await conn.execute(
            "UPDATE payments SET status='paid', paid_at=datetime('now'), updated_at=datetime('now') "
            "WHERE order_id=? AND status IN ('pending','link_sent')", (order_id,))
    elif new == "cancelled":
        await conn.execute(
            "UPDATE reminders SET sent=1, updated_at=datetime('now') WHERE order_id=? AND sent=0", (order_id,))


async def change_status(bot: Bot, order_id: int, new: str, actor: str,
                        reason: str | None = None) -> tuple[bool, str]:
    """Единственная точка смены статуса. Возвращает (успех, сообщение для того, кто запросил)."""
    o = await get_order_full(order_id)
    if o is None:
        return False, f"Заказ №{order_id} не найден."
    old = o["status"]
    if new == old:
        return False, f"Заказ №{order_id} уже в статусе «{texts.STATUS_LABELS[old]}»."
    if new not in TRANSITIONS.get(old, set()):
        return False, (f"Нельзя перевести заказ №{order_id} из «{texts.STATUS_LABELS[old]}» "
                       f"в «{texts.STATUS_LABELS[new]}».")
    try:
        async with db.transaction() as conn:
            extra, params = ("cancel_reason=?", (reason,)) if new == "cancelled" else ("", ())
            await _transition(conn, order_id, old, new, actor, extra, params)
    except StatusConflict:
        return False, f"Статус заказа №{order_id} уже изменился, проверьте /orders."
    delivered = await _after_transition(bot, order_id, old, new, actor)

    note = ""
    if new == "cancelled" and old in ("paid", "assigned", "in_progress"):
        note = " Заказ был оплачен — при необходимости оформите возврат: /refund " + str(order_id)
    if not delivered:
        note += _NOT_DELIVERED.format(phone=o["client_phone"] or "—")
    return True, f"Заказ №{order_id}: {texts.STATUS_LABELS[new]}.{note}"


_NOT_DELIVERED = "\n⚠️ Клиент не получил уведомление (заблокировал бота) — свяжитесь с ним по телефону {phone}."


async def _after_transition(bot: Bot, order_id: int, old: str, new: str, actor: str) -> bool:
    """Напоминания и уведомления после успешной смены статуса. True — клиент получил уведомление."""
    o = await get_order_full(order_id)

    if new == "paid":
        await schedule_slot_reminders(o)

    # --- клиенту: при каждом переходе ---
    text = texts.STATUS_CLIENT[new].format(id=order_id, executor=o["executor_name"] or "")
    markup = None
    if new == "completed":
        await notify(bot, o["client_tg_id"], text)
        text, markup = texts.REVIEW_ASK.format(id=order_id), kb.rating(order_id)
    delivered = await notify(bot, o["client_tg_id"], text, markup)

    # --- админам: если статус поменял не админ (клиент отменил, исполнитель начал/завершил) ---
    if not actor.startswith(("admin", "portal")):  # сам админ (в боте или в портале) знает, что сделал
        await notify_admins(bot, f"ℹ️ Заказ №{order_id}: {texts.STATUS_LABELS[old]} → "
                                 f"{texts.STATUS_LABELS[new]} ({actor})")
    return delivered


# ---------- Оплата ----------

async def generate_payment_link(order: dict) -> str | None:
    """ЗАГЛУШКА для будущей автоматизации (ЮKassa / СБП).
    Позже здесь будет запрос в платёжный сервис, возврат ссылки и сохранение payment_id.
    Пока возвращает None — ссылку админ отправляет вручную: /send_payment <id> <ссылка>."""
    return None


async def send_payment(bot: Bot, order_id: int, link: str, actor: str) -> tuple[bool, str]:
    """Сохраняет ссылку, отправляет клиенту, ставит напоминание об оплате через 3 часа."""
    o = await get_order_full(order_id)
    if o is None:
        return False, f"Заказ №{order_id} не найден."
    if o["status"] != "confirmed":
        return False, (f"Ссылку отправляют по подтверждённому заказу, а №{order_id} сейчас "
                       f"«{texts.STATUS_LABELS[o['status']]}». Сначала /confirm {order_id}.")
    await db.execute(
        "UPDATE payments SET status='link_sent', payment_link=?, link_sent_at=datetime('now'), "
        "updated_at=datetime('now') WHERE order_id=? AND status IN ('pending','link_sent')",
        (link, order_id),
    )
    await schedule_reminder(order_id, "pay_3h", now_utc() + timedelta(hours=3))
    have = payinfo.methods(order_id)
    delivered = await notify(bot, o["client_tg_id"],
                             f"💳 Ссылка для оплаты заказа №{order_id} на {rub(o['price_total'])}:",
                             kb.pay_options(order_id, link, sbp="sbp" in have, card="card" in have))
    if not delivered:
        return True, (f"Ссылка по заказу №{order_id} сохранена, но клиент её не получил (заблокировал бота)."
                      + _NOT_DELIVERED.format(phone=o["client_phone"] or "—") + f"\nСсылка: {link}")
    return True, f"Ссылка по заказу №{order_id} отправлена клиенту. Напомним ему через 3 часа, если не оплатит."


async def refund(bot: Bot, order_id: int) -> tuple[bool, str]:
    """Отмечает платёж возвращённым (сам перевод денег делаете вручную)."""
    n = await db.fetch_one("SELECT id FROM payments WHERE order_id=? AND status='paid'", (order_id,))
    if not n:
        return False, f"По заказу №{order_id} нет оплаченного платежа."
    await db.execute("UPDATE payments SET status='refunded', updated_at=datetime('now') WHERE id=?", (n["id"],))
    o = await get_order_full(order_id)
    await notify(bot, o["client_tg_id"], f"↩️ По заказу №{order_id} оформлен возврат {rub(o['price_total'])}.")
    return True, f"Платёж по заказу №{order_id} помечен как возвращённый."


# ---------- Исполнитель ----------

def calc_executor_pay(price_total: int, executor: dict) -> int:
    """Вознаграждение: процент от цены клиента или фикс за заказ."""
    if executor["rate_type"] == "percent":
        return round(price_total * executor["rate_value"] / 100)
    return round(executor["rate_value"])


async def assign_executor(bot: Bot, order_id: int, executor_id: int, actor: str) -> tuple[bool, str]:
    o = await get_order_full(order_id)
    ex = await db.fetch_one("SELECT * FROM executors WHERE id=?", (executor_id,))
    if o is None:
        return False, f"Заказ №{order_id} не найден."
    if ex is None or not ex["active"]:
        return False, f"Исполнитель {executor_id} не найден или отключён."
    if o["status"] != "paid":
        return False, (f"Исполнителя назначают на оплаченный заказ, а №{order_id} сейчас "
                       f"«{texts.STATUS_LABELS[o['status']]}».")
    if o["category"] not in ex["services"].split(","):
        return False, f"{ex['name']} не выполняет услуги категории «{o['category']}» (у него: {ex['services']})."
    if ex["legal_status"] == "individual" and ex["birth_year"]:
        if min_age(ex["birth_year"]) < 18 and not ex["parent_consent"]:
            return False, f"{ex['name']} несовершеннолетний, нет согласия родителей."

    pay = calc_executor_pay(o["price_total"], ex)
    margin = o["price_total"] - pay
    try:
        async with db.transaction() as conn:
            await _transition(conn, order_id, "paid", "assigned", actor,
                              "executor_id=?, executor_pay=?, margin=?", (executor_id, pay, margin))
            await conn.execute(
                "INSERT INTO executor_payouts (executor_id, order_id, amount) VALUES (?,?,?)",
                (executor_id, order_id, pay))
    except StatusConflict:
        return False, f"Статус заказа №{order_id} уже изменился."
    await _after_transition(bot, order_id, "paid", "assigned", actor)

    o = await get_order_full(order_id)
    start = from_db(o["start_at"])
    if start - timedelta(hours=2) > now_utc():
        await schedule_reminder(order_id, "executor_2h", start - timedelta(hours=2))
    if ex["tg_id"]:
        await notify(bot, ex["tg_id"],
                     f"🆕 Вам назначен заказ\n\n{order_card(o, for_staff=True)}\n\n"
                     f"Ваше вознаграждение: {rub(pay)}",
                     kb.executor_start(order_id))
    warn = "" if ex["tg_id"] else "\n⚠️ У исполнителя не указан Telegram ID — передайте ему заказ сами."
    pct = f"{margin / o['price_total'] * 100:.0f}%" if o["price_total"] else "—"
    return True, (f"Заказ №{order_id} → {ex['name']}.\nЦена клиента: {rub(o['price_total'])}\n"
                  f"Вознаграждение: {rub(pay)}\nВаша маржа: {rub(margin)} ({pct}){warn}")


# ---------- Напоминания (хранятся в БД, отправляет scheduler.py) ----------

async def schedule_reminder(order_id: int, kind: str, send_at: datetime) -> None:
    await db.execute(
        """INSERT INTO reminders (order_id, kind, send_at) VALUES (?,?,?)
           ON CONFLICT(order_id, kind) DO UPDATE SET send_at=excluded.send_at, sent=0,
                                                     updated_at=datetime('now')""",
        (order_id, kind, to_db(send_at)),
    )


async def schedule_slot_reminders(o: dict) -> None:
    """Клиенту: за 24 ч и за 2 ч до слота (если время ещё не прошло)."""
    start = from_db(o["start_at"])
    for kind, delta in (("client_24h", timedelta(hours=24)), ("client_2h", timedelta(hours=2))):
        if start - delta > now_utc():
            await schedule_reminder(o["id"], kind, start - delta)


# ---------- Заявки клиента по заказу ----------

REQUEST_KINDS = {"problem": "сообщил о проблеме", "move": "просит перенести"}
MAX_REQUEST = 500


async def order_history(order_id: int) -> list[dict]:
    """Журнал заказа по порядку: смены статусов и заявки клиента (с текстом в note)."""
    return await db.fetch_all(
        "SELECT to_status, actor, note, created_at FROM order_statuses_log WHERE order_id=? ORDER BY id",
        (order_id,))


async def client_request(bot, order_id: int, kind: str, text: str | None, actor: str) -> tuple[bool, str]:
    """Заявка клиента: проблема или просьба о переносе. Статус не меняется: заявка пишется в журнал
    и уходит администратору, который решает дальше."""
    if kind not in REQUEST_KINDS:
        return False, "Такой заявки нет."
    text = (text or "").strip()
    if not text:
        return False, "Напишите, пожалуйста, текстом: что случилось или на какое время перенести."
    if len(text) > MAX_REQUEST:
        return False, f"Слишком длинно: не больше {MAX_REQUEST} знаков."
    o = await get_order_full(order_id)
    if o is None:
        return False, f"Заказ №{order_id} не найден."
    if o["status"] not in ACTIVE_STATUSES:
        return False, f"Заказ №{order_id} уже завершён — напишите нам в чат."
    note = f"Клиент {REQUEST_KINDS[kind]}: {text}"
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO order_statuses_log (order_id, from_status, to_status, actor, note) VALUES (?,?,?,?,?)",
            (order_id, o["status"], o["status"], actor, note))
    await notify_admins(bot, f"📝 Заказ №{order_id} ({texts.STATUS_LABELS[o['status']]}). {note}")
    return True, "Передали администратору. Мы свяжемся с вами."


async def client_cancel(bot, order_id: int, user_id: int, actor: str) -> tuple[bool, str]:
    """Отмена клиентом до оплаты — те же правила, что в боте. Чужие заказы отменить нельзя."""
    o = await get_order_full(order_id)
    if o is None or o["user_id"] != user_id:
        return False, f"Заказ №{order_id} не найден."
    if o["status"] not in ("created", "confirmed"):
        return False, "Этот заказ уже нельзя отменить самостоятельно — напишите нам."
    return await change_status(bot, order_id, "cancelled", actor, "отменён клиентом")
