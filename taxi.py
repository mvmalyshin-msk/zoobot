"""Цена зоотакси по маршруту — по формуле Яндекс Такси.

Как считает Яндекс Go (тариф «Эконом», Москва, taxi.yandex.ru/moscow/tariff/econom):
минимальная стоимость 189 ₽ — в неё включены первые 3 минуты и 1 км; дальше по городу — не более
13 ₽ за каждый километр и 13 ₽ за каждую минуту в пути; опция «перевозка домашнего животного» —
не более 150 ₽. Сверху — надбавки из правил (ночь, срочно, за МКАД, крупная собака…), как у других услуг.

Минимальная стоимость — это цена услуги «Зоотакси» (меняется /price_set), остальные ставки —
командой /taxi_tariff (хранятся в kv). Расстояние и время — по дорогам: OSRM (OpenStreetMap,
бесплатно, без ключей); OSRM не знает пробок, поэтому время умножаем на поправку «пробки».
Если маршрутизатор не ответил — по прямой × извилистость дорог и средней скорости по Москве.
В запрос уходят только координаты двух точек, без имени и телефона клиента."""
import asyncio
import json
import logging
import math
import time
from dataclasses import asdict, dataclass, replace

import aiohttp

import db

log = logging.getLogger(__name__)

MINIMUM = 189            # посадка «Эконома»: включены incl_min минут и incl_km км
ROAD_FACTOR = 1.4        # дорога длиннее прямой (Москва: реки, ж/д, развязки)
CITY_KMH = 25            # средняя скорость такси по Москве днём, км/ч
OSRM_BASE = "https://router.project-osrm.org"
USER_AGENT = "ZooConciergeBot/1.0 (telegram bot for pet services)"
KV_KEY = "taxi_tariff"


@dataclass(frozen=True)
class Tariff:
    incl_min: int = 3        # минут включено в посадку
    incl_km: float = 1.0     # км включено в посадку
    per_km: int = 13         # ₽ за км сверх включённых
    per_min: int = 13        # ₽ за минуту сверх включённых
    pet: int = 150           # перевозка домашнего животного
    traffic: float = 1.5     # во сколько раз дольше из-за пробок, чем по пустой дороге


# название для админа → (поле, допустимые границы)
PARAMS = {
    "км": ("per_km", 0, 200),
    "мин": ("per_min", 0, 200),
    "животное": ("pet", 0, 5000),
    "включено_км": ("incl_km", 0, 30),
    "включено_мин": ("incl_min", 0, 60),
    "пробки": ("traffic", 1, 3),
}


@dataclass(frozen=True)
class Route:
    km: float
    minutes: int
    by_road: bool  # по дорогам (OSRM) или оценка по прямой

    @staticmethod
    def of(raw) -> "Route | None":
        """Маршрут из данных диалога (там он хранится словарём)."""
        if isinstance(raw, Route):
            return raw
        try:
            return Route(km=float(raw["km"]), minutes=int(raw["minutes"]), by_road=bool(raw["by_road"]))
        except (TypeError, KeyError, ValueError):
            return None

    def as_dict(self) -> dict:
        return asdict(self)


def fmt_km(km: float) -> str:
    """12.4 → «12,4»; 17.0 → «17»."""
    return f"{km:.1f}".rstrip("0").rstrip(".").replace(".", ",")


def fare(route: Route | None, t: Tariff, minimum: int, name: str) -> list[tuple[str, int]]:
    """Разбивка цены поездки, как в детализации Яндекс Go. Без маршрута — посадка и животное
    (остальное по счётчику: ставки написаны на экране подтверждения)."""
    items = [(f"{name}: посадка (включены {t.incl_min} мин и {fmt_km(t.incl_km)} км)", int(minimum))]
    if route:
        extra_km = max(0.0, route.km - t.incl_km)
        km_cost = round(extra_km * t.per_km)
        if km_cost:
            how = "по дорогам" if route.by_road else "примерно, по прямой с поправкой на дороги"
            items.append((f"Путь {fmt_km(route.km)} км {how}: {fmt_km(extra_km)} км × {t.per_km} ₽", km_cost))
        extra_min = max(0, route.minutes - t.incl_min)
        if extra_min * t.per_min:
            items.append((f"В пути ≈ {route.minutes} мин с учётом пробок: {extra_min} мин × {t.per_min} ₽",
                          extra_min * t.per_min))
    if t.pet:
        items.append(("Перевозка животного", int(t.pet)))
    return items


# ---------- Маршрут ----------

def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p = math.pi / 180
    h = (math.sin((b[0] - a[0]) * p / 2) ** 2
         + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(h))


def straight(a: tuple[float, float], b: tuple[float, float]) -> Route:
    """Запасная оценка, если маршрутизатор недоступен."""
    km = round(_km(a, b) * ROAD_FACTOR, 1)
    return Route(km=km, minutes=round(km / CITY_KMH * 60), by_road=False)


def parse_osrm(data, traffic: float) -> Route | None:
    """Ответ OSRM /route → маршрут (км с точностью 0,1; минуты с поправкой на пробки)."""
    try:
        if data.get("code") != "Ok":
            return None
        r = data["routes"][0]
        meters, seconds = float(r["distance"]), float(r["duration"])
    except (AttributeError, KeyError, IndexError, TypeError, ValueError):
        return None
    if not (math.isfinite(meters) and math.isfinite(seconds)) or meters < 0 or seconds < 0:
        return None
    return Route(km=round(meters / 1000, 1), minutes=math.ceil(round(seconds / 60 * traffic, 6)), by_road=True)


def osrm_path(a: tuple[float, float], b: tuple[float, float]) -> str:
    return (f"/route/v1/driving/{round(a[1], 6)},{round(a[0], 6)};{round(b[1], 6)},{round(b[0], 6)}"
            "?overview=false&alternatives=false")


_lock = asyncio.Lock()
_last_call = 0.0


async def _osrm(path: str):
    """GET к OSRM (публичный сервер просит не чаще 1 запроса в секунду). None — не ответил."""
    global _last_call
    async with _lock:
        wait = 1.0 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as s:
                async with s.get(OSRM_BASE + path) as r:
                    if r.status != 200:
                        log.warning("OSRM → HTTP %s", r.status)
                        return None
                    return await r.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning("OSRM недоступен: %s", type(e).__name__)
            return None
        finally:
            _last_call = time.monotonic()


async def route(a: tuple[float, float], b: tuple[float, float]) -> Route:
    """Маршрут от подачи до места назначения: по дорогам, а если не вышло — оценка по прямой."""
    t = await get_tariff()
    r = parse_osrm(await _osrm(osrm_path(a, b)), t.traffic)
    if r is None:
        r = straight(a, b)
    log.info("Маршрут такси: %s км, %s мин (%s)", fmt_km(r.km), r.minutes, "OSRM" if r.by_road else "по прямой")
    return r


# ---------- Ставки ----------

async def get_tariff() -> Tariff:
    row = await db.fetch_one("SELECT value FROM kv WHERE key=?", (KV_KEY,))
    t = Tariff()
    if not row:
        return t
    try:
        saved = json.loads(row["value"])
        fields = {f: type(getattr(t, f))(saved[f]) for f, _, _ in PARAMS.values() if f in saved}
    except (ValueError, TypeError):
        log.warning("Ставки такси в базе повреждены — беру стандартные")
        return t
    return replace(t, **fields)


async def set_param(name: str, value: str) -> Tariff:
    """Админ меняет ставку: /taxi_tariff км 17. ValueError — неизвестная ставка или число вне границ."""
    if name not in PARAMS:
        raise ValueError(f"Нет такой ставки: {name}. Есть: {', '.join(PARAMS)}")
    field, low, high = PARAMS[name]
    try:
        num = float(value.replace(",", "."))
    except ValueError:
        raise ValueError(f"«{value}» — не число") from None
    if not (math.isfinite(num) and low <= num <= high):
        raise ValueError(f"{name}: допустимо от {low} до {high}")
    t = await get_tariff()
    kind = type(getattr(t, field))
    t = replace(t, **{field: kind(num) if kind is float else int(round(num))})
    await db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (KV_KEY, json.dumps(asdict(t))))
    return t


def describe(t: Tariff, minimum: int) -> str:
    """Ставки для админа (команда /taxi_tariff без параметров) и подсказка клиенту без маршрута."""
    return (f"Посадка {minimum} ₽ (включены {t.incl_min} мин и {fmt_km(t.incl_km)} км), "
            f"далее {t.per_km} ₽/км и {t.per_min} ₽/мин, перевозка животного {t.pet} ₽")
