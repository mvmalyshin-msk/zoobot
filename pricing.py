"""Калькулятор цены.

Цена = базовая цена услуги × количество + надбавки из таблицы price_rules.
Правила хранятся в БД и меняются админ-командами (/rule_add, /rule_del, /price_set).
Сначала применяются фиксированные надбавки (+N ₽), потом процентные (от текущей суммы).
"""
import logging

import db
from utils import to_local

log = logging.getLogger(__name__)

NIGHT_START, NIGHT_END = 22, 7           # ночное время 22:00–07:00
NIGHT_UNITS = {"выгул", "час", "поездка"}  # для суточных услуг «ночь» не имеет смысла
UNIT_SHORT = {"час": "ч", "сутки": "сут."}


# ---------- Условия правил: каждое отвечает «применять ли правило к этому заказу» ----------

async def _pet_size_l(service: dict, p: dict) -> bool:
    return p.get("pet_size") == "L"


async def _night(service: dict, p: dict) -> bool:
    if service["unit"] not in NIGHT_UNITS or not p.get("start_at"):
        return False
    hour = to_local(p["start_at"]).hour
    return hour >= NIGHT_START or hour < NIGHT_END


async def _urgent_today(service: dict, p: dict) -> bool:
    return bool(p.get("is_urgent"))


async def _outside_mkad(service: dict, p: dict) -> bool:
    return bool(p.get("outside_mkad"))


async def _holiday(service: dict, p: dict) -> bool:
    if not p.get("start_at"):
        return False
    day = to_local(p["start_at"]).date().isoformat()
    return await db.fetch_one("SELECT 1 FROM holidays WHERE day=?", (day,)) is not None


CONDITIONS = {
    "pet_size_l": _pet_size_l,
    "night": _night,
    "urgent_today": _urgent_today,
    "outside_mkad": _outside_mkad,
    "holiday": _holiday,
}
VALID_CONDITIONS = tuple(CONDITIONS)


async def calculate_price(service_id: int, params: dict) -> tuple[int, list[tuple[str, int]]]:
    """Возвращает (итог в ₽, разбивка [(подпись, сумма), ...]).

    params:
      quantity     — часов / суток (для выгула и такси 1)
      start_at     — aware-datetime начала слота
      pet_size     — 'S' / 'M' / 'L' (только для собак, иначе None)
      is_urgent    — заказ «на сегодня»
      outside_mkad — выезд за МКАД
    """
    service = await db.fetch_one("SELECT * FROM services WHERE id=? AND active=1", (service_id,))
    if service is None:
        raise ValueError(f"Услуга {service_id} не найдена или отключена")

    qty = params.get("quantity") or 1
    base_total = round(service["base_price"] * qty)
    if service["unit"] in UNIT_SHORT:
        label = f"{service['name']}: {qty:g} {UNIT_SHORT[service['unit']]} × {service['base_price']} ₽"
    else:
        label = service["name"]
    breakdown: list[tuple[str, int]] = [(label, base_total)]
    total = base_total

    rules = await db.fetch_all(
        """SELECT * FROM price_rules
           WHERE active=1 AND (service_id IS NULL OR service_id=?)
           ORDER BY CASE effect_type WHEN 'fixed' THEN 0 ELSE 1 END, sort_order, id""",
        (service_id,),
    )
    for rule in rules:
        check = CONDITIONS.get(rule["condition_type"])
        if check is None:
            log.warning("Правило %s: неизвестное условие %s", rule["id"], rule["condition_type"])
            continue
        if not await check(service, params):
            continue
        if rule["effect_type"] == "fixed":
            add = round(rule["value"])
            name = rule["name"]
        else:
            add = round(total * rule["value"] / 100)
            name = f"{rule['name']} (+{rule['value']:g}%)"
        if add:
            total += add
            breakdown.append((name, add))

    return int(total), breakdown


def format_breakdown(breakdown: list[tuple[str, int]], total: int) -> str:
    """Построчная разбивка для клиента."""
    from utils import rub

    lines = []
    for i, (name, amount) in enumerate(breakdown):
        lines.append(f"• {name}: {rub(amount) if i == 0 else '+' + rub(amount)}")
    lines.append(f"\nИтого: {rub(total)}")
    return "\n".join(lines)
