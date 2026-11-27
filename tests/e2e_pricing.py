"""Калькулятор цены на настоящей БД: границы ночи, надбавки, праздники, суточные услуги.

Запуск:  BOT_TOKEN=1:x DB_PATH=/tmp/zoobot_pricing.db python tests/e2e_pricing.py
"""
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "1:x")
os.environ.setdefault("DB_PATH", "/tmp/zoobot_pricing.db")
for suffix in ("", "-wal", "-shm"):
    try:
        os.remove(os.environ["DB_PATH"] + suffix)
    except FileNotFoundError:
        pass

import db  # noqa: E402
import pricing  # noqa: E402
from config import config  # noqa: E402

FAILS = []


def at(y, mo, d, h=12, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=config.tz)


async def price(code, when, size=None, qty=1, urgent=False, outside=False):
    s = await db.fetch_one("SELECT id FROM services WHERE code=?", (code,))
    total, bd = await pricing.calculate_price(s["id"], {
        "quantity": qty, "start_at": when, "pet_size": size, "is_urgent": urgent, "outside_mkad": outside})
    return total


def check(title, got, want):
    good = got == want
    print(("  ✅ " if good else f"  ❌ (получено {got}, ждали {want}) ") + title)
    if not good:
        FAILS.append(title)


async def main():
    await db.init_db()
    await db.execute("UPDATE price_rules SET value=0 WHERE condition_type='always'")  # наценка сервиса проверяется отдельно ниже
    tue = (2026, 11, 10)  # обычный будний день
    print("Базовые случаи")
    check("выгул 60, день", await price("walk60", at(*tue)), 615)
    check("выгул 30, день", await price("walk30", at(*tue)), 500)
    check("крупная собака +20%", await price("walk60", at(*tue), size="L"), 738)
    check("М и S без надбавки", await price("walk60", at(*tue), size="M"), 615)
    check("срочно +300", await price("walk60", at(*tue), urgent=True), 915)
    check("за МКАД +150", await price("walk60", at(*tue), outside=True), 765)
    print("Ночь 22:00–07:00 (границы)")
    check("21:59 — ещё день", await price("walk60", at(*tue, 21, 59)), 615)
    check("22:00 — ночь ×1.3", await price("walk60", at(*tue, 22, 0)), 799)
    check("06:59 — ночь", await price("walk60", at(*tue, 6, 59)), 799)
    check("07:00 — уже день", await price("walk60", at(*tue, 7, 0)), 615)
    check("00:00 — ночь", await price("walk60", at(*tue, 0, 0)), 799)
    check("L + МКАД + ночь: (615+150)×1.2×1.3", await price("walk60", at(*tue, 23), size="L", outside=True), 1193)
    print("Другие услуги")
    check("зооняня 3 ч", await price("sitter_visit", at(*tue), qty=3), 2076)
    check("зооняня с проживанием 2 сут.: ночь не считается", await price("sitter_live", at(*tue, 23), qty=2), 5538)
    check("передержка дом 3 сут.", await price("boarding_home", at(*tue), qty=3), 1962)
    check("передержка отель 1 сут. + срочно", await price("boarding_hotel", at(*tue), urgent=True), 1531)
    check("такси днём без маршрута: минимум 1900", await price("taxi", at(*tue)), 1900)
    check("такси ночью: 1900 × 1.3", await price("taxi", at(*tue, 23)), 2470)
    print("Праздничные дни (+20%) должны работать без ручного заполнения")
    for y, mo, d, name in [(2026, 12, 31, "31 декабря 2026"), (2027, 1, 1, "1 января 2027"), (2027, 2, 23, "23 февраля 2027"),
                           (2027, 3, 8, "8 марта 2027"), (2027, 5, 1, "1 мая 2027"), (2027, 5, 9, "9 мая 2027"),
                           (2026, 11, 4, "4 ноября 2026")]:
        check(f"{name}: +20%", await price("walk60", at(y, mo, d)), 738)
    check("праздник + ночь: 615×1.3 затем ×1.2", await price("walk60", at(2026, 12, 31, 23)), 959)
    print("Наценка сервиса 30 % (как на боевом сайте)")
    await db.execute("UPDATE price_rules SET value=30 WHERE condition_type='always'")
    check("выгул 60 с наценкой: 615 → 799", await price("walk60", at(*tue)), 799)
    check("выгул 60, крупная собака, с наценкой: 799 + 20 %", await price("walk60", at(*tue), size="L"), 959)
    await db.execute("UPDATE price_rules SET value=0 WHERE condition_type='always'")
    print("Админ меняет цены без правки кода")
    await db.execute("UPDATE services SET base_price=1500 WHERE code='walk60'")
    check("/price_set 1500", await price("walk60", at(*tue)), 1500)
    await db.execute("UPDATE services SET active=0 WHERE code='walk60'")
    try:
        await price("walk60", at(*tue))
        check("выключенная услуга не считается", "посчитано", "ошибка")
    except ValueError:
        check("выключенная услуга не считается", "ошибка", "ошибка")
    print("\nИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not FAILS else f"ПРОВАЛЕНО {len(FAILS)}: {FAILS}")
    sys.exit(1 if FAILS else 0)


asyncio.run(main())
