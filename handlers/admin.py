"""Админ-команды. Доступны только Telegram ID из ADMIN_IDS; каждое действие пишется в admin_log."""
import asyncio
import logging
import sqlite3
from datetime import datetime

from aiogram import BaseMiddleware, F, Router
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

import db
import orders
import pricing
import texts
from config import config
from utils import fmt_local, min_age, now_utc, rub, to_db, to_local

log = logging.getLogger(__name__)
router = Router()
router.message.filter(F.from_user.id.in_(config.admin_ids))  # остальным эти команды «не существуют»

SERVICES = ("walk", "sitter", "boarding", "taxi")
LEGAL = ("self_employed", "ip", "legal", "individual")

HELP = """Команды админа

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
/stats — статистика за месяц
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
        text += f"\nДалее: /send_payment {ids[0]} <ссылка на оплату>"
    elif ok and new == "paid":
        text += f"\nДалее: /assign {ids[0]} <id исполнителя>"
    await m.answer(("✅ " if ok else "⚠️ ") + text)


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
