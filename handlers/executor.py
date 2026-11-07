"""Часть исполнителя: кнопки «Начал» / «Завершил» и команда /my со списком его заказов."""
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import orders
import texts
from utils import fmt_local

router = Router()

REPORT_WINDOW = 3600  # отчёт о выгуле принимаем в течение часа после завершения


class Report(StatesGroup):
    writing = State()


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
async def cb_executor(cb: CallbackQuery, state: FSMContext) -> None:
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
        if o["category"] == "walk":  # после выгула клиенту нужен отчёт: поведение, дела, фото
            await state.set_state(Report.writing)
            await state.update_data(report_order=order_id, report_until=time.time() + REPORT_WINDOW, report_first=True)
            await cb.message.answer(texts.ASK_REPORT.format(id=order_id), reply_markup=kb.report_done(order_id))


async def _report_target(m: Message, state: FSMContext):
    """Клиент, которому пойдёт отчёт; None, если час вышел или заказ чужой."""
    data = await state.get_data()
    order_id = data.get("report_order")
    if time.time() > data.get("report_until", 0):
        await state.clear()
        await m.answer(texts.REPORT_LATE.format(id=order_id))
        return None
    o = await orders.get_order_full(order_id)
    ex = await _executor_by_tg(m.from_user.id)
    if not o or not ex or o["executor_id"] != ex["id"]:
        await state.clear()
        return None
    return o


async def _send_header_once(m: Message, state: FSMContext, o: dict) -> None:
    if (await state.get_data()).get("report_first"):
        await orders.notify(m.bot, o["client_tg_id"], texts.REPORT_TO_CLIENT.format(id=o["id"]))
        await state.update_data(report_first=False)


@router.message(Report.writing, F.photo)
async def report_photo(m: Message, state: FSMContext) -> None:
    o = await _report_target(m, state)
    if o:
        await _send_header_once(m, state, o)
        await m.bot.send_photo(o["client_tg_id"], m.photo[-1].file_id, caption=(m.caption or "")[:900] or None)


@router.message(Report.writing, F.text, ~F.text.startswith("/"))
async def report_text(m: Message, state: FSMContext) -> None:
    o = await _report_target(m, state)
    if o:
        await _send_header_once(m, state, o)
        await orders.notify(m.bot, o["client_tg_id"], m.text[:3500])


@router.callback_query(Report.writing, F.data.startswith("rp:done:"))
async def report_done(cb: CallbackQuery, state: FSMContext) -> None:
    await cb.answer()
    await state.clear()
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(texts.REPORT_DONE)
