"""Исполнители в веб-админке: проверка формы, создание, правка, удаление.
Страницы рисует portal.py; здесь только правила и работа с таблицей executors (та же, что у команды /add_executor)."""
import re
import sqlite3

import db
from utils import min_age, normalize_phone, now_utc

KINDS = {"org": "Организация", "person": "Физлицо"}
TAX = {"self_employed": "Самозанятый", "ip": "ИП", "individual": "Физлицо без статуса (нужен год рождения)"}
SERVICES = {"walk": "🐕 Выгул", "sitter": "🏠 Зооняня", "boarding": "🛏 Передержка", "taxi": "🚕 Зоотакси"}
MAX_LINKS = 5

_TG = re.compile(r"@([A-Za-z0-9_]{5,32})")
_URL = re.compile(r"https?://[^\s/]+\.[^\s/]{2,}\S*")
_BARE = re.compile(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}/\S+")


class Duplicate(Exception):
    """Исполнитель с таким Telegram ID уже есть."""


def kind_of(row) -> str:
    return "org" if row["legal_status"] == "legal" else "person"


def name_label(kind: str) -> str:
    return "Название организации" if kind == "org" else "ФИО"


def _link(line: str) -> str | None:
    line = line.strip()
    if m := _TG.fullmatch(line):
        return f"https://t.me/{m[1]}"
    if len(line) > 200 or re.search(r"\s", line):
        return None
    if _URL.fullmatch(line):
        return line
    if _BARE.fullmatch(line) and "://" not in line:
        return "https://" + line
    return None


def parse_links(text: str) -> tuple[list[str], str | None]:
    out = []
    for raw in (text or "").replace("\r", "").split("\n"):
        if not raw.strip():
            continue
        link = _link(raw)
        if link is None:
            return [], f"Не похоже на ссылку: «{raw.strip()[:40]}». Нужно https://… или @имя в Telegram."
        out.append(link)
    if len(out) > MAX_LINKS:
        return [], f"Ссылок слишком много: не больше {MAX_LINKS}."
    return out, None


def _getall(form, key) -> list[str]:
    return [str(v) for v in form.getall(key, [])] if hasattr(form, "getall") else list(form.get(key, []))


def parse(form) -> tuple[dict, dict]:
    """Форма → (чистые значения для таблицы, ошибки по полям). Ошибок нет — словарь ошибок пуст."""
    g = lambda k: str(form.get(k, "") or "").strip()  # noqa: E731
    err: dict[str, str] = {}
    kind = g("kind")
    if kind not in KINDS:
        err["kind"] = "Выберите: организация или физлицо."
    name = g("name")
    if not 2 <= len(name) <= 120:
        err["name"] = f"{name_label(kind)}: от 2 до 120 знаков."
    phone = normalize_phone(g("phone"))
    if not phone:
        err["phone"] = "Контактный телефон: российский номер, например +7 900 111-22-33."
    links, bad = parse_links(g("links"))
    if bad:
        err["links"] = bad
    services = [s for s in _getall(form, "services") if s]
    if not services or any(s not in SERVICES for s in services):
        err["services"] = "Отметьте хотя бы одну услугу из списка."
    legal, birth, parent = "legal", None, 0
    if kind == "person":
        legal = g("tax") if g("tax") in TAX else "self_employed"
        if legal == "individual":
            birth = int(g("birth_year")) if g("birth_year").isdigit() else None
            parent = 1 if g("parent_consent") else 0
            if not birth or not 1900 <= birth <= now_utc().year:
                err["birth_year"] = "Для физлица без статуса укажите год рождения: нужно проверить возраст."
            elif min_age(birth) < 14:
                err["birth_year"] = "Физлицам младше 14 лет работать нельзя."
            elif min_age(birth) < 18 and not parent:
                err["parent_consent"] = "Исполнителю 14–17 лет нужно согласие родителей: поставьте галочку."
    rate_type = g("rate_type") or "percent"
    try:
        rate_value = float(g("rate_value").replace(",", "."))
        if rate_type not in ("percent", "fixed") or rate_value <= 0 or (rate_type == "percent" and rate_value > 100):
            raise ValueError
    except ValueError:
        err["rate"] = "Ставка: процент от 1 до 100 или сумма в рублях больше нуля."
        rate_value = 70.0
    tg_raw = g("tg_id")
    tg_id = None
    if tg_raw:
        if tg_raw.isdigit():
            tg_id = int(tg_raw)
        else:
            err["tg_id"] = "Telegram ID — только цифры (его показывает бот @userinfobot)."
    clean = {
        "legal_status": legal, "name": name, "phone": phone, "links": "\n".join(links) or None,
        "active": 0 if g("status") == "0" else 1, "services": ",".join(s for s in SERVICES if s in services),
        "districts": g("districts")[:200], "rate_type": rate_type, "rate_value": rate_value,
        "birth_year": birth, "parent_consent": parent, "payout_details": g("payout")[:300] or None, "tg_id": tg_id,
    }
    return clean, err


_COLS = ("tg_id", "name", "phone", "links", "legal_status", "services", "districts", "rate_type", "rate_value",
         "birth_year", "parent_consent", "payout_details", "active")


async def create(clean: dict) -> int:
    try:
        return await db.execute(
            f"INSERT INTO executors ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})",
            tuple(clean[c] for c in _COLS))
    except sqlite3.IntegrityError as e:
        raise Duplicate from e


async def update(eid: int, clean: dict) -> None:
    try:
        await db.execute(
            f"UPDATE executors SET {', '.join(c + '=?' for c in _COLS)}, {db.touch()} WHERE id=?",
            (*(clean[c] for c in _COLS), eid))
    except sqlite3.IntegrityError as e:
        raise Duplicate from e


async def delete(eid: int) -> tuple[str, str]:
    """deleted — удалён; blocked — есть заказы или выплаты (историю не теряем, предлагаем «Неактивен»); missing — нет такого."""
    ex = await db.fetch_one("SELECT * FROM executors WHERE id=?", (eid,))
    if ex is None:
        return "missing", "Исполнитель не найден: возможно, его уже удалили."
    n = (await db.fetch_one("SELECT (SELECT COUNT(*) FROM orders WHERE executor_id=?) + "
                            "(SELECT COUNT(*) FROM executor_payouts WHERE executor_id=?) + "
                            "(SELECT COUNT(*) FROM reviews WHERE executor_id=?) AS n", (eid, eid, eid)))["n"]
    if n:
        return "blocked", ("У исполнителя есть заказы, выплаты или отзывы, поэтому удалить его нельзя: пропадёт история. "
                           "Поставьте статус «Неактивен»: он исчезнет из списка для назначения.")
    async with db.transaction() as conn:
        if ex["tg_id"]:
            await conn.execute("DELETE FROM messages WHERE party_tg_id=? AND party_kind='executor'", (ex["tg_id"],))
        await conn.execute("DELETE FROM executors WHERE id=?", (eid,))
    return "deleted", "Исполнитель удалён."
