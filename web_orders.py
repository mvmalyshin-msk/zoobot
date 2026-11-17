"""Заявка с сайта → заказ бота. Админ указывает время и количество, цену считает pricing по тем же правилам, что и в
Telegram. Зоотакси здесь не оформляется: маршрут для цены считается по адресам в боте."""
import logging
from datetime import timedelta

import breeds
import db
import orders
import pricing
import texts
from utils import now_utc, to_local

log = logging.getLogger(__name__)


def _pet_fields(raw: str | None) -> tuple[str, str, str | None]:
    """«Дымок, кот, 3 года» → (кличка, вид, заметки). Вид угадываем по слову, иначе «другое»."""
    parts = [x.strip() for x in str(raw or "").replace(";", ",").split(",") if x.strip()]
    name = (parts[0] if parts else "Питомец")[:60]
    species = "другое"
    for x in parts[1:] or parts:
        sp = breeds.parse_species(x.split()[0]) if x.split() else None
        if sp:
            species = sp
            break
    return name, species, (str(raw)[:300] if raw else None)


def _end(service: dict, start, qty: int):
    if service["unit"] == "час":
        return start + timedelta(hours=qty)
    if service["unit"] == "сутки":
        return start + timedelta(days=qty)
    if service["code"] == "walk30":
        return start + timedelta(minutes=30)
    if service["code"] == "walk60":
        return start + timedelta(minutes=60)
    return None


async def make_order(bot, web_rid: int, start_at, qty: int, actor: str) -> tuple[bool, str, int | None]:
    """(успех, сообщение админу, номер заказа). Повторный вызов возвращает уже оформленный заказ."""
    r = await db.fetch_one("SELECT * FROM web_requests WHERE id=?", (web_rid,))
    if not r:
        return False, "Заявка не найдена.", None
    if r["order_id"]:
        return False, f"Заказ уже оформлен: №{r['order_id']}.", r["order_id"]
    if r["status"] == "rejected":
        return False, "Заявка отменена: сначала верните её в работу.", None
    svc = await db.fetch_one("SELECT * FROM services WHERE id=?", (r["service_id"],))
    if not svc:
        return False, "У заявки нет услуги.", None
    if svc["category"] == "taxi":
        return False, "Зоотакси оформляется в боте: там считается маршрут.", None
    user = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (r["web_uid"],)) if r["web_uid"] else None
    if not user:
        return False, "У заявки нет клиента в базе: оформите заказ через чат.", None
    qty = max(1, min(int(qty or 1), 60))
    if start_at <= now_utc():
        return False, "Это время уже прошло.", None
    name, species, notes = _pet_fields(r["pet"])
    try:
        pet_id = await db.execute(
            "INSERT INTO pets (user_id, name, species, size, notes) VALUES (?,?,?,?,?)",
            (user["id"], name, species, "M" if species == "собака" else None, notes))
        params = {"quantity": qty, "start_at": start_at, "pet_size": "M" if species == "собака" else None,
                  "is_urgent": to_local(start_at).date() == to_local(now_utc()).date(),
                  "is_express": bool(r["express"]),
                  "outside_mkad": False, "pet_traits": None, "pet_notes": notes or "", "pet_species": species,
                  "route": None}
        total, breakdown = await pricing.calculate_price(svc["id"], params)
    except Exception:
        log.exception("web_orders: цена заявки %s", web_rid)
        return False, "Не удалось посчитать цену: оформите заказ через бота.", None
    oid = await orders.create_order(
        {"user_id": user["id"], "pet_id": pet_id, "service_id": svc["id"], "start_at": start_at,
         "end_at": _end(svc, start_at, qty), "quantity": qty, "address": r["address"], "address_to": None,
         "is_urgent": params["is_urgent"], "outside_mkad": False, "address_unverified": True,
         "comment": r["comment"]},
        total, breakdown, actor=actor)
    await db.execute(f"UPDATE web_requests SET order_id=?, status='work', {db.touch()} WHERE id=? AND order_id IS NULL",
                     (oid, web_rid))
    try:
        await orders.notify(bot, r["web_uid"], texts.ORDER_ACCEPTED.format(id=oid))
    except Exception:
        log.exception("web_orders: сообщение клиенту по заявке %s", web_rid)
    return True, f"Заказ №{oid} оформлен: {total} ₽.", oid
