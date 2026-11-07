"""Часть исполнителя: кнопки «Начал» / «Завершил» и команда /my со списком его заказов."""
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import orders
import texts
from utils import fmt_local

router = Router()


async def _executor_by_tg(tg_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM executors WHERE tg_id=? AND active=1", (tg_id,))


@router.message(Command("my"))
async def cmd_my(m: Message) -> None:
    ex = await _executor_by_tg(m.from_user.id)
    if not ex:
        return  # обычному клиенту эта команда ничего не даёт
    rows = await db.fetch_all(
        """SELECT o.id, o.status, o.start_at, s.name FROM orders o JOIN services s ON s.id=o.service_id
           WHERE o.executor_id=? AND o.status IN ('assigned','in_progress') ORDER BY o.start_at""", (ex["id"],))
    if not rows:
        await m.answer("Назначенных заказов нет.")
        return
    for r in rows:
        o = await orders.get_order_full(r["id"])
        markup = kb.executor_start(r["id"]) if r["status"] == "assigned" else kb.executor_done(r["id"])
        await m.answer(orders.order_card(o, for_staff=True), reply_markup=markup)


@router.callback_query(lambda c: c.data and c.data.startswith("ex:"))
async def cb_executor(cb: CallbackQuery) -> None:
    _, action, order_id = cb.data.split(":")
    order_id = int(order_id)
    ex = await _executor_by_tg(cb.from_user.id)
    o = await orders.get_order_full(order_id)
    # исполнитель управляет только своими заказами
    if not ex or not o or o["executor_id"] != ex["id"]:
        await cb.answer("Это не ваш заказ", show_alert=True)
        return
    await cb.answer()
    new = "in_progress" if action == "start" else "completed"
    ok, text = await orders.change_status(cb.bot, order_id, new, f"executor:{cb.from_user.id}")
    if not ok:
        await cb.message.answer(text)
        return
    await cb.message.edit_reply_markup(reply_markup=None)
    if new == "in_progress":
        await cb.message.answer(f"▶️ Заказ №{order_id} начат. Когда закончите — нажмите:",
                                reply_markup=kb.executor_done(order_id))
    else:
        await cb.message.answer(f"✅ Заказ №{order_id} завершён. Спасибо! Выплата будет оформлена.")
