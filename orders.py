"""Бизнес-логика заказов: создание, машина статусов, оплата, назначение исполнителя, уведомления.

Статусы:  created → confirmed → paid → assigned → in_progress → completed
          (из любого незавершённого можно уйти в cancelled)
Платёж:   pending → link_sent → paid → refunded
"""
import json
import logging
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

import db
import keyboards as kb
import texts
from config import config
from utils import fmt_local, from_db, now_utc, rub, to_db

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
                  p.name AS pet_name, p.species AS pet_species, p.breed AS pet_breed,
                  p.size AS pet_size, p.notes AS pet_notes,
                  e.name AS executor_name, e.tg_id AS executor_tg_id, e.phone AS executor_phone
           FROM orders o
           JOIN services s ON s.id = o.service_id
           JOIN users u ON u.id = o.user_id
           LEFT JOIN pets p ON p.id = o.pet_id
           LEFT JOIN executors e ON e.id = o.executor_id
           WHERE o.id = ?""",
        (order_id,),
    )


def order_card(o: dict, *, for_staff: bool = False) -> str:
    """Текстовая карточка заказа. for_staff=True добавляет контакты клиента и заметки о питомце."""
    lines = [f"Заказ №{o['id']} · {texts.STATUS_LABELS[o['status']]}", f"Услуга: {o['service_name']}"]
    when = fmt_local(o["start_at"])
    if o["end_at"] and o["unit"] in ("час", "сутки"):
        when += f" → {fmt_local(o['end_at'])}"
    lines.append(f"Когда: {when}")
    if o["address_to"]:
        lines.append(f"Откуда: {o['address']}\nКуда: {o['address_to']}")
    else:
        lines.append(f"Адрес: {o['address']}")
    if o["outside_mkad"]:
        lines.append("За МКАД: да")
    if for_staff and o.get("address_unverified"):
        lines.append("⚠️ Адрес не подтверждён картой — уточните у клиента")
    if o["pet_name"]:
        size = f", {o['pet_size']}" if o["pet_size"] else ""
        pet = f"Питомец: {o['pet_name']} ({o['pet_species']}{size})"
        if o["pet_breed"]:
            pet += f", {o['pet_breed']}"
        lines.append(pet)
        if for_staff and o["pet_notes"]:
            lines.append(f"Особенности: {o['pet_notes']}")
    if for_staff:
        lines.append(f"Клиент: {o['client_name']}, {o['client_phone']}")
    lines.append(f"Сумма: {rub(o['price_total'])}")
    return "\n".join(lines)


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


async def notify_admins(bot: Bot, text: str) -> None:
    for admin_id in config.admin_ids:
        await notify(bot, admin_id, text)


# ---------- Создание заказа ----------

async def create_order(data: dict, total: int, breakdown: list, actor: str) -> int:
    """data: user_id, pet_id, service_id, start_at(datetime), end_at(datetime|None), quantity,
    address, address_to, is_urgent, outside_mkad. Цена и разбивка фиксируются в заказе."""
    params = {"quantity": data["quantity"], "is_urgent": data["is_urgent"]}
    async with db.transaction() as conn:
        cur = await conn.execute(
            """INSERT INTO orders (user_id, pet_id, service_id, status, start_at, end_at, quantity,
                                   address, address_to, is_urgent, outside_mkad, params_json,
                                   price_total, price_breakdown, address_unverified)
               VALUES (?,?,?,'created',?,?,?,?,?,?,?,?,?,?,?)""",
            (data["user_id"], data["pet_id"], data["service_id"], to_db(data["start_at"]),
             to_db(data["end_at"]) if data.get("end_at") else None, data["quantity"],
             data["address"], data.get("address_to"), int(data["is_urgent"]),
             int(data["outside_mkad"]), json.dumps(params, ensure_ascii=False), total,
             json.dumps(breakdown, ensure_ascii=False), int(data.get("address_unverified", False))),
        )
        order_id = cur.lastrowid
        await conn.execute(
            "INSERT INTO order_statuses_log (order_id, from_status, to_status, actor) VALUES (?,NULL,'created',?)",
            (order_id, actor),
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
    if new not in TRANSITIONS.get(old, set()):
        return False, (f"Нельзя перевести заказ №{order_id} из «{texts.STATUS_LABELS[old]}» "
                       f"в «{texts.STATUS_LABELS[new]}».")
    try:
        async with db.transaction() as conn:
            extra, params = ("cancel_reason=?", (reason,)) if new == "cancelled" else ("", ())
            await _transition(conn, order_id, old, new, actor, extra, params)
    except StatusConflict:
        return False, f"Статус заказа №{order_id} уже изменился, проверьте /orders."
    await _after_transition(bot, order_id, old, new, actor)

    note = ""
    if new == "cancelled" and old in ("paid", "assigned", "in_progress"):
        note = " Заказ был оплачен — при необходимости оформите возврат: /refund " + str(order_id)
    return True, f"Заказ №{order_id}: {texts.STATUS_LABELS[new]}.{note}"


async def _after_transition(bot: Bot, order_id: int, old: str, new: str, actor: str) -> None:
    """Напоминания и уведомления после успешной смены статуса."""
    o = await get_order_full(order_id)

    if new == "paid":
        await schedule_slot_reminders(o)

    # --- клиенту: при каждом переходе ---
    text = texts.STATUS_CLIENT[new].format(id=order_id, executor=o["executor_name"] or "")
    markup = None
    if new == "completed":
        await notify(bot, o["client_tg_id"], text)
        text, markup = texts.REVIEW_ASK.format(id=order_id), kb.rating(order_id)
    await notify(bot, o["client_tg_id"], text, markup)

    # --- админам: если статус поменял не админ (клиент отменил, исполнитель начал/завершил) ---
    if not actor.startswith("admin"):
        await notify_admins(bot, f"ℹ️ Заказ №{order_id}: {texts.STATUS_LABELS[old]} → "
                                 f"{texts.STATUS_LABELS[new]} ({actor})")


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
    await notify(bot, o["client_tg_id"],
                 f"💳 Ссылка для оплаты заказа №{order_id} на {rub(o['price_total'])}:",
                 kb.pay_button(link))
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
        age = now_utc().year - ex["birth_year"]
        if age < 18 and not ex["parent_consent"]:
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
