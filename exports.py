"""База клиентов и отчёт по статусам: Excel «по запросу» (кнопки в меню администратора и в портале).

Файл собирается из базы заново при каждой выгрузке: клиенты, заказы, заявки с сайта, история смен статусов и сводка по статусам.
Тексты клиентов, начинающиеся с «=», «+», «-», «@», экранируются (report.safe), чтобы Excel не принял их за формулу."""
import io
from datetime import datetime, timedelta

import openpyxl
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

import db
import texts
from config import config
from report import safe
from utils import fmt_local, to_db

STATUS_ORDER = ("created", "confirmed", "paid", "assigned", "in_progress", "completed", "cancelled")


def _sheet(wb, title, header, rows, first=False):
    ws = wb.active if first else wb.create_sheet()
    ws.title = title
    ws.append(header)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append([safe(v) for v in r])
    for i, h in enumerate(header, 1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if r[i - 1] is not None] or [8])
        ws.column_dimensions[get_column_letter(i)].width = min(width + 2, 45)
    ws.freeze_panes = "A2"


async def status_summary() -> list[dict]:
    """По каждому статусу: сколько заказов сейчас, на какую сумму, сколько смен в этот статус сегодня и за 7 дней."""
    now = datetime.now(config.tz)
    today0 = to_db(now.replace(hour=0, minute=0, second=0, microsecond=0))
    week0 = to_db(now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=6))
    have = {r["status"]: r for r in await db.fetch_all("SELECT status, COUNT(*) AS n, COALESCE(SUM(price_total),0) AS total FROM orders GROUP BY status")}
    moved = {}
    for r in await db.fetch_all("SELECT to_status, SUM(created_at >= ?) AS today, SUM(created_at >= ?) AS week FROM order_statuses_log GROUP BY to_status", (today0, week0)):
        moved[r["to_status"]] = r
    out = []
    for st in STATUS_ORDER:
        h, m = have.get(st), moved.get(st)
        out.append({"status": st, "label": texts.STATUS_LABELS[st], "n": h["n"] if h else 0, "total": h["total"] if h else 0,
                    "today": int(m["today"] or 0) if m else 0, "week": int(m["week"] or 0) if m else 0})
    return out


async def status_report_text() -> str:
    rows = await status_summary()
    web_new = (await db.fetch_one("SELECT COUNT(*) AS n FROM web_requests WHERE status='new'"))["n"]
    lines = ["📈 Отчёт по статусам", "", "Сейчас:"] + [f"• {r['label']}: {r['n']}" for r in rows]
    lines += ["", "Перешли в статус сегодня / за 7 дней:"] + [f"• {r['label']}: {r['today']} / {r['week']}" for r in rows if r["today"] or r["week"]]
    if not any(r["today"] or r["week"] for r in rows):
        lines.append("• пока ничего")
    lines += ["", f"Новых заявок с сайта: {web_new}"]
    return "\n".join(lines)


async def build_database_xlsx() -> bytes:
    clients = await db.fetch_all(
        """SELECT u.id, u.tg_id, u.created_at, u.name, u.phone, u.tg_username, u.district, u.address, u.pdn_consent,
                  (SELECT GROUP_CONCAT(p.name, ', ') FROM pets p WHERE p.user_id=u.id AND p.is_active=1) AS pets,
                  (SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id) AS n_orders,
                  (SELECT COALESCE(SUM(o.price_total),0) FROM orders o WHERE o.user_id=u.id AND o.status='completed') AS done_sum,
                  (SELECT MAX(o.created_at) FROM orders o WHERE o.user_id=u.id) AS last_order
           FROM users u ORDER BY u.created_at, u.id""")
    orders = await db.fetch_all(
        """SELECT o.id, o.created_at, u.name, u.phone, s.name AS service, o.status, o.start_at, o.address, o.price_total,
                  e.name AS executor FROM orders o JOIN users u ON u.id=o.user_id JOIN services s ON s.id=o.service_id
           LEFT JOIN executors e ON e.id=o.executor_id ORDER BY o.id""")
    reqs = await db.fetch_all(
        """SELECT r.id, r.created_at, r.name, r.phone, s.name AS service, r.status, r.when_text, r.address, r.comment
           FROM web_requests r LEFT JOIN services s ON s.id=r.service_id ORDER BY r.id""")
    log = await db.fetch_all("SELECT created_at, order_id, from_status, to_status, actor FROM order_statuses_log ORDER BY id")
    wb = openpyxl.Workbook()
    _sheet(wb, "Клиенты",
           ["№", "Дата регистрации", "Имя", "Телефон", "Telegram", "Источник", "Район", "Адрес", "Питомцы", "Заказов",
            "Сумма выполненных, ₽", "Последний заказ", "Согласие на ПДн"],
           [[c["id"], fmt_local(c["created_at"]), c["name"], c["phone"], f"@{c['tg_username']}" if c["tg_username"] else None,
             "Сайт" if (c["tg_id"] or 0) < 0 else "Telegram", c["district"], c["address"], c["pets"], c["n_orders"],
             c["done_sum"], fmt_local(c["last_order"]) if c["last_order"] else None, "да" if c["pdn_consent"] else "нет"] for c in clients],
           first=True)
    _sheet(wb, "Заказы", ["№", "Создан", "Клиент", "Телефон", "Услуга", "Статус", "Дата услуги", "Адрес", "Сумма, ₽", "Исполнитель"],
           [[o["id"], fmt_local(o["created_at"]), o["name"], o["phone"], o["service"], texts.STATUS_LABELS.get(o["status"], o["status"]),
             fmt_local(o["start_at"]), o["address"], o["price_total"], o["executor"]] for o in orders])
    _sheet(wb, "Заявки с сайта", ["№", "Создана", "Имя", "Телефон", "Услуга", "Статус", "Когда", "Адрес", "Комментарий"],
           [[r["id"], fmt_local(r["created_at"]), r["name"], r["phone"], r["service"], r["status"], r["when_text"], r["address"],
             r["comment"]] for r in reqs])
    _sheet(wb, "История статусов", ["Когда", "Заказ №", "Было", "Стало", "Кто"],
           [[fmt_local(h["created_at"]), h["order_id"], texts.STATUS_LABELS.get(h["from_status"], h["from_status"]),
             texts.STATUS_LABELS.get(h["to_status"], h["to_status"]), h["actor"]] for h in log])
    _sheet(wb, "Статусы", ["Статус", "Заказов сейчас", "Сумма, ₽", "Перешли сегодня", "Перешли за 7 дней"],
           [[r["label"], r["n"], r["total"], r["today"], r["week"]] for r in await status_summary()])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
