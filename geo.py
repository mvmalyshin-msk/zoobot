"""Проверка адреса по карте (OpenStreetMap Nominatim: бесплатно, без ключей).

Что делает: по тексту клиента находит до 5 настоящих адресов в Москве и области, определяет район и
«за МКАД ли это». Сеть изолирована в `_get_json`, остальное — чистые функции (их проверяют тесты).
Правила Nominatim: не чаще 1 запроса в секунду, честный User-Agent. Личные данные клиента и почта
владельца в запрос не попадают — только текст адреса.
"""
import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass

import aiohttp

import breeds

log = logging.getLogger(__name__)

BASE = "https://nominatim.openstreetmap.org"
USER_AGENT = "ZooConciergeBot/1.0 (telegram bot for pet services)"
# Москва и область: left,top,right,bottom (долгота/широта)
VIEWBOX = (35.1, 56.97, 40.3, 54.2)
OK_STATES = {"москва", "московская область"}
# Территории с состоянием «Москва», которые лежат за МКАД
OUTSIDE_PARTS = ("зеленоград", "новомосковск", "троицк")
MKAD_CENTER = (55.7558, 37.6173)
MKAD_RADIUS_KM = 21  # запасной способ, если у точки нет области в адресе

_lock = asyncio.Lock()
_last_call = 0.0
_cache: dict[str, list] = {}


@dataclass(frozen=True)
class Place:
    label: str          # как показать и сохранить: «Железнодорожная улица, 33, Сходня, Химки»
    district: str       # район/город для администратора
    lat: float
    lon: float
    outside_mkad: bool
    has_house: bool     # есть ли номер дома (без него адрес приблизительный)


# ---------- Чистые функции ----------

def looks_like_address(text: str) -> bool:
    """Форма адреса: либо есть цифра (дом), либо хотя бы два слова; без мата и клавиатурной каши."""
    t = " ".join(text.split())
    if not 5 <= len(t) <= 200 or breeds.is_obscene(t):
        return False
    words = re.findall(r"[a-zа-яё]{3,}", t.lower().replace("ё", "е"))
    if not words:
        return False
    if not all(breeds.looks_like_text(w, 3) for w in words):
        return False
    return bool(re.search(r"\d", t)) or len(words) >= 2


_FLAT = re.compile(r"[,\s]+(?:кв(?:артира)?\.?|оф(?:ис)?\.?)\s*№?\s*(\w+)\s*$", re.I)


def split_flat(text: str) -> tuple[str, str | None]:
    """«Ленина 1, кв. 5» → («Ленина 1», «5»). Карты квартир не знают, поэтому отделяем."""
    t = " ".join(text.split())
    m = _FLAT.search(t)
    if not m:
        return t, None
    return t[:m.start()].strip(" ,"), m.group(1)


def query_variants(text: str, limit: int = 3) -> list[str]:
    """Что пробовать искать: как написано, без квартиры, без хвостовых чисел (корпус/квартира без «кв»)."""
    t = " ".join(text.split())
    out = [t]
    base, _ = split_flat(t)
    out.append(base)
    tokens = base.replace(",", " ").split()
    while len(tokens) > 2 and re.fullmatch(r"\d+\w?", tokens[-1]):
        tokens = tokens[:-1]
        out.append(" ".join(tokens))
    seen: list[str] = []
    for v in out:
        if v and v not in seen:
            seen.append(v)
    return seen[:limit]


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _outside(addr: dict, lat: float, lon: float) -> bool:
    state = (addr.get("state") or "").lower()
    if state == "московская область":
        return True
    if state == "москва":
        parts = " ".join(str(addr.get(k, "")) for k in ("suburb", "city_district", "borough", "city")).lower()
        return any(p in parts for p in OUTSIDE_PARTS)
    return _km(lat, lon, *MKAD_CENTER) > MKAD_RADIUS_KM


def parse_nominatim(items: list[dict], limit: int = 5) -> list[Place]:
    """Ответ Nominatim → список мест. Отбрасывает не Россию, не Москву/область, дубли."""
    left, top, right, bottom = VIEWBOX
    out: list[Place] = []
    for it in items:
        try:
            addr = it.get("address") or {}
            lat, lon = float(it["lat"]), float(it["lon"])
            if (addr.get("country_code") or "ru").lower() != "ru":
                continue
            if not (bottom <= lat <= top and left <= lon <= right):
                continue
            state = (addr.get("state") or "").lower()
            if state and state not in OK_STATES:
                continue
            road = addr.get("road") or addr.get("pedestrian") or addr.get("residential") or it.get("name") or ""
            house = addr.get("house_number") or ""
            town = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("hamlet") \
                or addr.get("municipality") or ""
            sub = addr.get("suburb") or addr.get("city_district") or ""
            parts = [p for p in (road, house, sub if sub != town else "", town) if p]
            label = ", ".join(parts) or str(it.get("display_name", ""))[:90]
            district = sub or town or addr.get("state") or "Московская область"
            p = Place(label=label[:120], district=district[:60], lat=lat, lon=lon,
                      outside_mkad=_outside(addr, lat, lon), has_house=bool(house))
        except (KeyError, ValueError, TypeError):
            continue
        if all(p.label != q.label for q in out):
            out.append(p)
        if len(out) >= limit:
            break
    return out


# ---------- Сеть ----------

async def _get_json(path: str, params: dict) -> list | dict | None:
    """GET к Nominatim с паузой ≥1.1 с между запросами. None — сервис недоступен."""
    global _last_call
    async with _lock:
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            timeout = aiohttp.ClientTimeout(total=6)
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": USER_AGENT}) as s:
                async with s.get(BASE + path, params=params) as r:
                    if r.status != 200:
                        log.warning("Nominatim %s → HTTP %s", path, r.status)
                        return None
                    return await r.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            log.warning("Nominatim недоступен: %s", e)
            return None
        finally:
            _last_call = time.monotonic()


async def search(text: str) -> list[Place] | None:
    """Варианты адреса. [] — такого адреса нет на карте; None — карта недоступна (тогда не блокируем клиента)."""
    key = " ".join(text.lower().split())
    if key in _cache:
        return _cache[key]
    for q in query_variants(text):
        data = await _get_json("/search", {
            "q": q, "format": "jsonv2", "addressdetails": 1, "limit": 8, "countrycodes": "ru",
            "accept-language": "ru", "bounded": 1,
            "viewbox": ",".join(str(x) for x in VIEWBOX)})
        if data is None:  # сервис не отвечает — остальные варианты бессмысленны
            return None
        places = parse_nominatim(data)
        if places:
            if len(_cache) > 300:
                _cache.clear()
            _cache[key] = places
            return places
    return []


async def reverse(lat: float, lon: float) -> Place | None:
    """Адрес по геопозиции из Telegram."""
    data = await _get_json("/reverse", {"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 18,
                                        "addressdetails": 1, "accept-language": "ru"})
    if not isinstance(data, dict) or "error" in data:
        return None
    places = parse_nominatim([data])
    return places[0] if places else None
