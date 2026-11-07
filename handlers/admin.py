"""Админ-команды. Доступны только Telegram ID из ADMIN_IDS; каждое действие пишется в admin_log."""
import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import orders
import payinfo
import pricing
import texts
from config import config
from utils import fmt_local, min_age, now_utc, rub, to_db, to_local

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id.in_(config.admin_ids))  # остальным эти команды «не существуют»
router.callback_query.filter(F.from_user.id.in_(config.admin_ids))

SERVICES = ("walk", "sitter", "boarding", "taxi")
LEGAL = ("self_employed", "ip", "legal", "individual")

HELP = """Команды админа

Кнопки (команды копировать не нужно): новая заявка приходит с «Подтвердить / Отклонить / Чат с клиентом»;
ссылку на оплату просто вставьте сообщением — бот поймёт заказ и отправит; «Оплата пришла» отмечает оплату.

Заказы:
/orders — активные заказы
/confirm <id> — подтвердить заказ
/send_payment <id> <ссылка> — отправить клиенту ссылку на оплату
/paid <id> — отметить оплату полученной
/assign <id> <исполнитель> — назначить исполнителя (покажет маржу)
/progress <id> — в работе · /complete <id> — выполнен
/cancel <id> [причина] — отменить
/refund <id> — отметить возврат платежа

Цены:
/services — услуги и цены
/price_set <id услуги> <цена>
/service_active <id услуги> <0|1>
/rules — правила надбавок
/rule_add <id услуги или 0> <условие> <fixed|percent> <значение> <название>
   условия: pet_size_l, night, urgent_today, outside_mkad, holiday
/rule_del <id правила>
/holiday_add <ГГГГ-ММ-ДД> [название] · /holiday_del <дата>

Исполнители:
/executors — список, статусы, рейтинг
/add_executor Имя | телефон | статус | услуги | районы | ставка | tg_id | год рождения | согласие родителей 0/1 | реквизиты
   статус: self_employed, ip, legal, individual; услуги: walk,sitter,boarding,taxi
   ставка: 70% (доля от цены) или 500 (фикс за заказ); необязательные поля можно оставить пустыми

Прочее:
/webadmin — одноразовая ссылка, чтобы открыть бота в браузере под своим аккаунтом (без Telegram и VPN)
/stats — статистика за месяц
/report [ГГГГ-ММ-ДД] — Excel: новые клиенты и заказы за день (сам приходит в 23:55)
/broadcast <текст> — рассылка тем, кто дал согласие"""


class AdminLogMiddleware(BaseMiddleware):
    """Пишет в admin_log каждую выполненную админ-команду."""

    async def __call__(self, handler, event: Message, data):
        text = event.text or ""
        if text.startswith("/"):
            cmd, _, args = text.partition(" ")
            await db.execute("INSERT INTO admin_log (admin_tg_id, command, args) VALUES (?,?,?)",
                             (event.from_user.id, cmd.split("@")[0], args[:500]))
        return await handler(event, data)


router.message.middleware(AdminLogMiddleware())


async def reply_long(m: Message, text: str) -> None:
    """Telegram режет сообщения длиннее 4096 символов — отправляем кусками."""
    while text:
        chunk, text = text[:3900], text[3900:]
        if text and "\n" in chunk:  # режем по границе строки
            cut = chunk.rfind("\n")
            chunk, text = chunk[:cut], chunk[cut:].lstrip("\n") + text
        await m.answer(chunk)


def _ids(command: CommandObject, n: int) -> list[int] | None:
    """Разбирает n целых чисел из аргументов команды."""
    parts = (command.args or "").split()
    if len(parts) < n or not all(p.lstrip("-").isdigit() for p in parts[:n]):
        return None
    return [int(p) for p in parts[:n]]


@router.message(Command("admin", "help_admin"))
async def cmd_help(m: Message) -> None:
    await m.answer(HELP)


# ---------- Заказы и статусы ----------

@router.message(Command("orders"))
async def cmd_orders(m: Message) -> None:
    rows = await db.fetch_all(
        f"""SELECT o.id, o.status, o.start_at, o.price_total, s.name AS service, u.name AS client,
                   u.phone, e.name AS executor
            FROM orders o JOIN services s ON s.id=o.service_id JOIN users u ON u.id=o.user_id
            LEFT JOIN executors e ON e.id=o.executor_id
            WHERE o.status IN ({','.join('?' * len(orders.ACTIVE_STATUSES))})
            ORDER BY o.start_at""", orders.ACTIVE_STATUSES)
    if not rows:
        await m.answer("Активных заказов нет.")
        return
    lines = [f"Активные заказы ({len(rows)}):", ""]
    for r in rows:
        ex = f" · {r['executor']}" if r["executor"] else ""
        lines.append(f"№{r['id']} [{texts.STATUS_LABELS[r['status']]}] {fmt_local(r['start_at'])}\n"
                     f"   {r['service']} · {rub(r['price_total'])} · {r['client']} {r['phone']}{ex}")
    await reply_long(m, "\n".join(lines))


async def _simple_status(m: Message, command: CommandObject, new: str, reason: str | None = None) -> None:
    ids = _ids(command, 1)
    if not ids:
        await m.answer("Укажите номер заказа, например: /confirm 12")
        return
    ok, text = await orders.change_status(m.bot, ids[0], new, f"admin:{m.from_user.id}", reason)
    if ok and new == "confirmed":
        text += "\nДалее: просто пришлите сюда ссылку на оплату (или ничего: клиент сам увидит дежурные реквизиты)."
        await _offer_methods(m.bot, ids[0])
    elif ok and new == "paid":
        text += f"\nДалее: /assign {ids[0]} <id исполнителя>"
    await m.answer(("✅ " if ok else "⚠️ ") + text)


async def _offer_methods(bot, order_id: int) -> bool:
    """Клиенту сразу после подтверждения уходят дежурные способы оплаты (СБП / карта) и кнопка «Я оплатил»."""
    have = payinfo.methods(order_id)
    if not have:
        return False
    o = await orders.get_order_full(order_id)
    return await orders.notify(
        bot, o["client_tg_id"], f"Выберите, как удобнее оплатить заказ №{order_id} ({rub(o['price_total'])}):",
        kb.pay_options(order_id, None, sbp="sbp" in have, card="card" in have))


@router.message(Command("confirm"))
async def cmd_confirm(m: Message, command: CommandObject) -> None:
    await _simple_status(m, command, "confirmed")


@router.message(Command("paid"))
async def cmd_paid(m: Message, command: CommandObject) -> None:
    await _simple_status(m, command, "paid")


@router.message(Command("progress"))
async def cmd_progress(m: Message, command: CommandObject) -> None:
    await _simple_status(m, command, "in_progress")


@router.message(Command("complete"))
async def cmd_complete(m: Message, command: CommandObject) -> None:
    await _simple_status(m, command, "completed")


@router.message(Command("cancel"))
async def cmd_cancel(m: Message, command: CommandObject) -> None:
    reason = " ".join((command.args or "").split()[1:]) or "отменён администратором"
    await _simple_status(m, command, "cancelled", reason)


@router.message(Command("send_payment"))
async def cmd_send_payment(m: Message, command: CommandObject) -> None:
    parts = (command.args or "").split()
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].startswith(("http://", "https://")):
        await m.answer("Формат: /send_payment <id заказа> <ссылка https://...>")
        return
    ok, text = await orders.send_payment(m.bot, int(parts[0]), parts[1], f"admin:{m.from_user.id}")
    await m.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(Command("assign"))
async def cmd_assign(m: Message, command: CommandObject) -> None:
    ids = _ids(command, 2)
    if not ids:
        await m.answer("Формат: /assign <id заказа> <id исполнителя>")
        return
    ok, text = await orders.assign_executor(m.bot, ids[0], ids[1], f"admin:{m.from_user.id}")
    await m.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(Command("refund"))
async def cmd_refund(m: Message, command: CommandObject) -> None:
    ids = _ids(command, 1)
    if not ids:
        await m.answer("Формат: /refund <id заказа>")
        return
    ok, text = await orders.refund(m.bot, ids[0])
    await m.answer(("✅ " if ok else "⚠️ ") + text)


# ---------- Цены и правила ----------

@router.message(Command("services"))
async def cmd_services(m: Message) -> None:
    rows = await db.fetch_all("SELECT * FROM services ORDER BY id")
    await m.answer("\n".join(
        f"{s['id']}. {s['name']} — {rub(s['base_price'])}/{s['unit']} ({s['code']})"
        f"{'' if s['active'] else ' · ОТКЛЮЧЕНА'}" for s in rows))


@router.message(Command("price_set"))
async def cmd_price_set(m: Message, command: CommandObject) -> None:
    ids = _ids(command, 2)
    if not ids or ids[1] <= 0:
        await m.answer("Формат: /price_set <id услуги> <цена в ₽>. Список: /services")
        return
    s = await db.fetch_one("SELECT * FROM services WHERE id=?", (ids[0],))
    if not s:
        await m.answer("Нет такой услуги. Список: /services")
        return
    await db.execute(f"UPDATE services SET base_price=?, {db.touch()} WHERE id=?", (ids[1], ids[0]))
    await m.answer(f"✅ {s['name']}: {rub(s['base_price'])} → {rub(ids[1])}")


@router.message(Command("service_active"))
async def cmd_service_active(m: Message, command: CommandObject) -> None:
    ids = _ids(command, 2)
    if not ids or ids[1] not in (0, 1):
        await m.answer("Формат: /service_active <id услуги> <0|1>")
        return
    await db.execute(f"UPDATE services SET active=?, {db.touch()} WHERE id=?", (ids[1], ids[0]))
    await m.answer("✅ Готово. Список: /services")


@router.message(Command("rules"))
async def cmd_rules(m: Message) -> None:
    rows = await db.fetch_all(
        """SELECT r.*, s.name AS service FROM price_rules r LEFT JOIN services s ON s.id=r.service_id
           WHERE r.active=1 ORDER BY r.id""")
    if not rows:
        await m.answer("Правил нет.")
        return
    await m.answer("\n".join(
        f"{r['id']}. {r['name']} · {r['condition_type']} · "
        f"{'+' + format(r['value'], 'g') + (' ₽' if r['effect_type'] == 'fixed' else '%')} · "
        f"{r['service'] or 'все услуги'}" for r in rows))


@router.message(Command("rule_add"))
async def cmd_rule_add(m: Message, command: CommandObject) -> None:
    usage = ("Формат: /rule_add <id услуги или 0 = все> <условие> <fixed|percent> <значение> <название>\n"
             f"Условия: {', '.join(pricing.VALID_CONDITIONS)}\n"
             "Пример: /rule_add 0 holiday percent 20 Праздничный день")
    parts = (command.args or "").split(maxsplit=4)
    if len(parts) < 5:
        await m.answer(usage)
        return
    sid, cond, effect, value, name = parts
    try:
        sid_i, val = int(sid), float(value.replace(",", "."))
    except ValueError:
        await m.answer(usage)
        return
    if cond not in pricing.VALID_CONDITIONS or effect not in ("fixed", "percent") or val <= 0:
        await m.answer(usage)
        return
    if sid_i and not await db.fetch_one("SELECT 1 FROM services WHERE id=?", (sid_i,)):
        await m.answer("Нет такой услуги. Список: /services")
        return
    rid = await db.execute(
        "INSERT INTO price_rules (service_id, name, condition_type, effect_type, value, sort_order) "
        "VALUES (?,?,?,?,?,?)", (sid_i or None, name[:60], cond, effect, val, 100))
    await m.answer(f"✅ Правило №{rid} добавлено.")


@router.message(Command("rule_del"))
async def cmd_rule_del(m: Message, command: CommandObject) -> None:
    ids = _ids(command, 1)
    if not ids:
        await m.answer("Формат: /rule_del <id правила>. Список: /rules")
        return
    # не удаляем строку, а отключаем: старые заказы сохраняют свою разбивку, история не теряется
    await db.execute(f"UPDATE price_rules SET active=0, {db.touch()} WHERE id=?", (ids[0],))
    await m.answer(f"✅ Правило №{ids[0]} отключено.")


@router.message(Command("holiday_add"))
async def cmd_holiday_add(m: Message, command: CommandObject) -> None:
    parts = (command.args or "").split(maxsplit=1)
    try:
        day = datetime.strptime(parts[0], "%Y-%m-%d").date().isoformat()
    except (IndexError, ValueError):
        await m.answer("Формат: /holiday_add 2026-12-31 Новый год")
        return
    await db.execute("INSERT OR REPLACE INTO holidays (day, title) VALUES (?,?)",
                     (day, parts[1] if len(parts) > 1 else None))
    await m.answer(f"✅ {day} теперь праздничный день.")


@router.message(Command("holiday_del"))
async def cmd_holiday_del(m: Message, command: CommandObject) -> None:
    await db.execute("DELETE FROM holidays WHERE day=?", ((command.args or "").strip(),))
    await m.answer("✅ Готово.")


# ---------- Исполнители ----------

@router.message(Command("executors"))
async def cmd_executors(m: Message) -> None:
    rows = await db.fetch_all(
        """SELECT e.*, (SELECT AVG(rating) FROM reviews r WHERE r.executor_id=e.id) AS avg_rating,
                  (SELECT COUNT(*) FROM reviews r WHERE r.executor_id=e.id) AS reviews_cnt
           FROM executors e ORDER BY e.id""")
    if not rows:
        await m.answer("Исполнителей пока нет. Добавить: /add_executor (подсказка в /admin)")
        return
    lines = []
    for e in rows:
        rate = f"{e['rate_value']:g}%" if e["rate_type"] == "percent" else rub(e["rate_value"])
        rating = f"⭐ {e['avg_rating']:.1f} ({e['reviews_cnt']})" if e["reviews_cnt"] else "нет отзывов"
        lines.append(f"{e['id']}. {e['name']} · {e['legal_status']} · {e['services']} · "
                     f"{e['districts'] or 'районы не указаны'} · ставка {rate} · {rating}"
                     f"{'' if e['active'] else ' · НЕ АКТИВЕН'}")
    await reply_long(m, "\n".join(lines))


@router.message(Command("add_executor"))
async def cmd_add_executor(m: Message, command: CommandObject) -> None:
    f = [x.strip() for x in (command.args or "").split("|")]
    f += [""] * (10 - len(f))
    name, phone, status, services, districts, rate, tg_id, birth, parent, payout = f[:10]
    usage = "Формат и пример — в /admin (раздел «Исполнители»)."

    if not name or status not in LEGAL or not rate:
        await m.answer("Нужны минимум: имя, статус, услуги, ставка. " + usage)
        return
    svc = [s.strip() for s in services.split(",") if s.strip()]
    if not svc or any(s not in SERVICES for s in svc):
        await m.answer(f"Услуги — из списка: {', '.join(SERVICES)}")
        return
    try:
        if rate.endswith("%"):
            rate_type, rate_value = "percent", float(rate[:-1].replace(",", "."))
            if not 0 < rate_value <= 100:
                raise ValueError
        else:
            rate_type, rate_value = "fixed", float(rate.replace(",", "."))
            if rate_value <= 0:
                raise ValueError
        tg = int(tg_id) if tg_id.isdigit() else None
        birth_year = int(birth) if birth.isdigit() else None
    except ValueError:
        await m.answer("Ставка: «70%» или сумма «500». " + usage)
        return

    parent_ok = parent == "1"
    if status == "individual":
        if not birth_year:
            await m.answer("Для физлица укажите год рождения (8-е поле): нужно проверить возраст.")
            return
        age = min_age(birth_year)
        if age < 14:
            await m.answer("Физлицам младше 14 лет работать нельзя (считаем осторожно: по году рождения, "
                           "без точной даты).")
            return
        if age < 18 and not parent_ok:
            await m.answer("Исполнителю 14–17 лет нужно согласие родителей: поставьте 1 в поле «согласие родителей».")
            return
    try:
        eid = await db.execute(
            """INSERT INTO executors (tg_id, name, phone, legal_status, services, districts,
                                      rate_type, rate_value, birth_year, parent_consent, payout_details)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tg, name, phone or None, status, ",".join(svc), districts, rate_type, rate_value,
             birth_year, int(parent_ok), payout or None))
    except sqlite3.IntegrityError:
        await m.answer("Исполнитель с таким Telegram ID уже есть.")
        return
    await m.answer(f"✅ Исполнитель №{eid} добавлен: {name}. Назначить на заказ: /assign <заказ> {eid}")


# ---------- Статистика ----------

def _pct(part: int, whole: int) -> str:
    return f"{part / whole * 100:.0f}%" if whole else "—"


@router.message(Command("report"))
async def cmd_report(m: Message, command: CommandObject) -> None:
    arg = (command.args or "").strip()
    try:
        day = datetime.strptime(arg, "%Y-%m-%d").date() if arg else to_local(now_utc()).date()
    except ValueError:
        await m.answer("Дата в формате ГГГГ-ММ-ДД, например /report 2026-11-07")
        return
    await _send_report(m, day)


async def _send_report(m: Message, day) -> None:
    import report
    day = day or to_local(now_utc()).date()
    if not await report.send_daily_report(m.bot, day, always=True):
        await m.answer("Не получилось отправить отчёт, подробности в журнале.")


@router.message(Command("stats"))
async def cmd_stats(m: Message) -> None:
    now_l = to_local(now_utc())
    start = to_db(now_l.replace(day=1, hour=0, minute=0, second=0, microsecond=0))

    t = await db.fetch_one(
        "SELECT COUNT(*) total, COALESCE(SUM(status='cancelled'),0) cancelled "
        "FROM orders WHERE created_at >= ?", (start,))
    paid = await db.fetch_one(
        """SELECT COUNT(*) n FROM orders o WHERE o.created_at >= ? AND EXISTS
           (SELECT 1 FROM order_statuses_log l WHERE l.order_id=o.id AND l.to_status='paid')""", (start,))
    money = await db.fetch_one(
        """SELECT AVG(price_total) avg_check, COALESCE(SUM(margin),0) margin, COALESCE(SUM(price_total),0) revenue
           FROM orders WHERE created_at >= ? AND status IN ('paid','assigned','in_progress','completed')""", (start,))
    clients = await db.fetch_one(
        "SELECT COUNT(DISTINCT user_id) n FROM orders WHERE created_at >= ?", (start,))
    repeat = await db.fetch_one(
        """SELECT COUNT(*) n FROM (SELECT user_id FROM orders
              WHERE status!='cancelled' AND user_id IN (SELECT DISTINCT user_id FROM orders WHERE created_at >= ?)
              GROUP BY user_id HAVING COUNT(*) >= 2)""", (start,))

    total = t["total"]
    await m.answer(
        f"📊 Статистика с {now_l.replace(day=1).strftime('%d.%m.%Y')}\n\n"
        f"Заявок: {total}\n"
        f"Оплачено: {paid['n']} (конверсия заявка→оплата {_pct(paid['n'], total)})\n"
        f"Отмен: {t['cancelled']} ({_pct(t['cancelled'], total)})\n"
        f"Средний чек (оплаченные): {rub(money['avg_check']) if money['avg_check'] else '—'}\n"
        f"Выручка: {rub(money['revenue'])}\n"
        f"Маржа (по назначенным заказам): {rub(money['margin'])}\n"
        f"Клиентов с заказом: {clients['n']}, из них повторных: {repeat['n']} ({_pct(repeat['n'], clients['n'])})")


# ---------- Рассылка: только тем, кто дал согласие ----------

@router.message(Command("broadcast"))
async def cmd_broadcast(m: Message, command: CommandObject) -> None:
    text = (command.args or "").strip()
    if not text:
        await m.answer("Формат: /broadcast <текст>. Получат только клиенты, давшие согласие на рассылки.")
        return
    users = await db.fetch_all(
        "SELECT tg_id FROM users WHERE marketing_consent=1 AND pdn_consent=1 AND is_blocked=0")
    footer = "\n\nОтписаться: /profile → «Отключить рассылки»"
    sent = failed = 0
    for u in users:
        try:
            await m.bot.send_message(u["tg_id"], text + footer)
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await m.bot.send_message(u["tg_id"], text + footer)
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            await db.execute(f"UPDATE users SET is_blocked=1, {db.touch()} WHERE tg_id=?", (u["tg_id"],))
            failed += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # ~20 сообщений/сек — в пределах лимитов Telegram
    await m.answer(f"📣 Рассылка завершена: доставлено {sent}, не доставлено {failed}, "
                   f"получателей с согласием: {len(users)}.")


# ---------- Кнопки вместо команд ----------

_pending_links: dict[int, str] = {}  # ссылка, которую админ прислал, когда ждущих оплаты заказов несколько
_URL = r"^\s*https?://\S+\s*$"


async def _chat(order_id: int) -> str:
    o = await orders.get_order_full(order_id)
    return orders.chat_url(o["client_username"], o["client_tg_id"])


async def _send_link(m_or_cb, bot, order_id: int, link: str, admin_id: int):
    ok, text = await orders.send_payment(bot, order_id, link, f"admin:{admin_id}")
    return ("✅ " if ok else "⚠️ ") + text


@router.message(F.text.regexp(_URL))
async def msg_payment_link(m: Message) -> None:
    """Админ просто вставляет ссылку: бот сам понимает, к какому заказу она относится."""
    link = m.text.strip()
    pend = await orders.pending_payment_orders()
    if not pend:
        await m.answer("Нет подтверждённых заказов, которые ждут оплаты. Сначала подтвердите заказ.")
        return
    if len(pend) == 1:
        await m.answer(await _send_link(m, m.bot, pend[0]["id"], link, m.from_user.id))
        return
    _pending_links[m.from_user.id] = link
    await m.answer("К какому заказу эта ссылка?", reply_markup=kb.link_choose(pend))


# ---------- Чат с клиентом из браузера (у него нет Telegram, переписка идёт через бота) ----------

_relay: dict[int, int] = {}  # админ → id веб-клиента, которому он сейчас отвечает


@router.message(Command("webadmin"))
async def cmd_webadmin(m: Message) -> None:
    import webchat
    link = await webchat.make_login_link(m.from_user.id)
    await m.answer("🔐 Бот в браузере для администратора. Ссылка одноразовая, действует 10 минут "
                   "(откройте её на телефоне, нажмите «Войти»):\n" + link +
                   "\nКлиентам даётся обычная ссылка без входа: " + f"{config.webapp_url}/chat")


@router.callback_query(F.data.startswith("wc:"))
async def cb_web_client(cb: CallbackQuery) -> None:
    await cb.answer()
    cid = int(cb.data[3:])
    _relay[cb.from_user.id] = cid
    row = await db.fetch_one("SELECT name FROM users WHERE tg_id=?", (cid,))
    await cb.message.answer(f"💬 Чат с клиентом {row['name'] if row else cid}. Всё, что вы напишете сейчас, "
                            "уйдёт ему в браузер. Команды и ссылки на оплату работают как обычно.",
                            reply_markup=kb.relay_end())


@router.callback_query(F.data == "wcx")
async def cb_web_client_end(cb: CallbackQuery) -> None:
    await cb.answer()
    _relay.pop(cb.from_user.id, None)
    await cb.message.answer("Чат с клиентом закрыт. Сообщения клиента снова придут с кнопкой «Ответить».")


def _relaying(m: Message) -> bool:
    return bool(m.text) and not m.text.startswith("/") and m.from_user.id in _relay


@router.message(_relaying)
async def msg_relay(m: Message) -> None:
    cid = _relay[m.from_user.id]
    ok = await orders.notify(m.bot, cid, "👤 Администратор:\n" + m.text)
    await m.answer("✅ Отправлено клиенту" if ok else "⚠️ Не доставлено")


@router.callback_query(F.data.startswith("lnk:"))
async def cb_link_pick(cb: CallbackQuery) -> None:
    await cb.answer()
    link = _pending_links.pop(cb.from_user.id, None)
    if not link:
        await cb.message.answer("Ссылку я уже не помню. Пришлите её ещё раз.")
        return
    await cb.message.answer(await _send_link(cb, cb.bot, int(cb.data[4:]), link, cb.from_user.id))


def _parse(data: str, n: int) -> list[int] | None:
    parts = data.split(":")[2:]
    if len(parts) != n or not all(p.isdigit() for p in parts):
        return None
    return [int(p) for p in parts]


@router.callback_query(F.data.startswith("ap:"))
async def cb_approve(cb: CallbackQuery) -> None:
    await cb.answer()
    action = cb.data.split(":")[1]
    nums = _parse(cb.data, 2 if action == "as" else 1)
    if nums is None:
        await cb.message.answer(texts.STALE)
        return
    order_id, actor = nums[0], f"admin:{cb.from_user.id}"
    if action == "ok":
        ok, text = await orders.change_status(cb.bot, order_id, "confirmed", actor)
        if ok:
            await _offer_methods(cb.bot, order_id)
            text += "\nПришлите сюда ссылку на оплату — я пойму, к какому заказу она, и отправлю клиенту."
        await cb.message.answer(("✅ " if ok else "⚠️ ") + text,
                                reply_markup=kb.admin_after_confirm(order_id, await _chat(order_id),
                                                                    methods=bool(payinfo.methods(order_id))) if ok else None)
    elif action == "no":
        ok, text = await orders.change_status(cb.bot, order_id, "cancelled", actor, "отклонён администратором")
        await cb.message.answer(("✅ " if ok else "⚠️ ") + text)
    elif action == "methods":
        sent = await _offer_methods(cb.bot, order_id)
        await cb.message.answer("✅ Способы оплаты отправлены клиенту." if sent else
                                "Реквизиты не настроены: задайте PAY_SBP_PHONE или PAY_CARD в Railway → Variables.")
    elif action == "paid":
        ok, text = await orders.change_status(cb.bot, order_id, "paid", actor)
        if not ok:
            await cb.message.answer("⚠️ " + text)
            return
        o = await orders.get_order_full(order_id)
        execs = [e for e in await db.fetch_all("SELECT id, name, services FROM executors WHERE active=1")
                 if o["category"] in e["services"].split(",")]
        await cb.message.answer(f"✅ {text}\nНазначьте исполнителя:", reply_markup=kb.admin_assign(order_id, execs))
    elif action == "nopay":
        o = await orders.get_order_full(order_id)
        if o:
            await orders.notify(cb.bot, o["client_tg_id"], texts.PAY_NOT_FOUND.format(id=order_id))
        await cb.message.answer("Клиенту написала, что оплату пока не видно.")
    elif action == "as":
        ok, text = await orders.assign_executor(cb.bot, order_id, nums[1], actor)
        await cb.message.answer(("✅ " if ok else "⚠️ ") + text)
    else:
        await cb.message.answer(texts.STALE)


async def _rules_text() -> tuple[str, list]:
    rows = await db.fetch_all(
        """SELECT r.*, s.name AS service FROM price_rules r LEFT JOIN services s ON s.id=r.service_id
           WHERE r.active=1 ORDER BY r.id""")
    return ("⚙️ Наценки. ➖/➕ меняют значение (±10 % или ±50 ₽), 🗑 отключает правило. "
            "Новое правило: /rule_add." if rows else "Правил нет. Добавить: /rule_add."), rows


@router.callback_query(F.data.startswith("rl:"))
async def cb_rule_change(cb: CallbackQuery) -> None:
    await cb.answer()
    parts = cb.data.split(":")
    if len(parts) != 3 or not parts[2].isdigit() or parts[1] not in ("dn", "up", "off"):
        await cb.message.answer(texts.STALE)
        return
    rid = int(parts[2])
    r = await db.fetch_one("SELECT * FROM price_rules WHERE id=? AND active=1", (rid,))
    if r:
        if parts[1] == "off":
            await db.execute(f"UPDATE price_rules SET active=0, {db.touch()} WHERE id=?", (rid,))
        else:
            step = (50 if r["effect_type"] == "fixed" else 10) * (1 if parts[1] == "up" else -1)
            await db.execute(f"UPDATE price_rules SET value=?, {db.touch()} WHERE id=?",
                             (max(0, r["value"] + step), rid))
    text, rows = await _rules_text()
    try:
        await cb.message.edit_text(text, reply_markup=kb.rules_panel(rows))
    except Exception:  # сообщение старое или не изменилось
        await cb.message.answer(text, reply_markup=kb.rules_panel(rows))


@router.callback_query(F.data.startswith("adm:"))
async def cb_admin_menu(cb: CallbackQuery) -> None:
    await cb.answer()
    where = cb.data[4:]
    msg = cb.message
    if where == "home":
        await msg.answer(texts.ADMIN_MENU_TEXT, reply_markup=kb.admin_menu())
    elif where == "orders":
        await cmd_orders(msg)
    elif where == "report":
        await _send_report(msg, None)
    elif where == "services":
        await cmd_services(msg)
    elif where == "rules":
        text, rows = await _rules_text()
        await msg.answer(text, reply_markup=kb.rules_panel(rows))
    elif where == "execs":
        await cmd_executors(msg)
    elif where == "pay":
        have = payinfo.methods(0)
        await msg.answer("Дежурные способы оплаты: " + (", ".join({"sbp": "СБП", "card": "карта"}[k] for k in have)
                         if have else "не настроены") + ".\nРеквизиты задаются в Railway → Variables: "
                         "PAY_SBP_PHONE, PAY_SBP_BANK, PAY_CARD, PAY_RECIPIENT (в чат их присылать не нужно).")
    elif where == "help":
        await msg.answer(HELP)
    else:
        await msg.answer(texts.STALE)
