"""Операции с клиентами, согласиями и питомцами."""
import re

import db

USER_FIELDS = {"name", "phone", "phone_verified", "district", "address", "outside_mkad", "address_verified", "contact_pref"}


async def get_or_create_user(tg_user) -> dict:
    """Находит клиента по Telegram ID или создаёт. Сбрасывает флаг блокировки бота."""
    user = await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (tg_user.id,))
    if user is None:
        await db.execute(
            "INSERT INTO users (tg_id, tg_username) VALUES (?, ?)", (tg_user.id, tg_user.username)
        )
        return await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (tg_user.id,))
    if user["tg_username"] != tg_user.username or user["is_blocked"]:
        await db.execute(
            f"UPDATE users SET tg_username=?, is_blocked=0, {db.touch()} WHERE id=?",
            (tg_user.username, user["id"]),
        )
        user = await db.fetch_one("SELECT * FROM users WHERE id=?", (user["id"],))
    return user


async def get_user_by_tg(tg_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM users WHERE tg_id=?", (tg_id,))


async def get_user(user_id: int) -> dict | None:
    return await db.fetch_one("SELECT * FROM users WHERE id=?", (user_id,))


async def save_consent(user: dict, ctype: str, granted: bool, version: str) -> None:
    """Пишет строку в журнал согласий и обновляет быстрый флаг в users."""
    flag_col = "pdn_consent" if ctype == "pdn" else "marketing_consent"
    async with db.transaction() as conn:
        await conn.execute(
            "INSERT INTO consents (user_id, tg_id, type, granted, text_version) VALUES (?,?,?,?,?)",
            (user["id"], user["tg_id"], ctype, int(granted), version),
        )
        await conn.execute(
            f"UPDATE users SET {flag_col}=?, {db.touch()} WHERE id=?", (int(granted), user["id"])
        )


async def update_user(user_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in USER_FIELDS}
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    await db.execute(
        f"UPDATE users SET {sets}, {db.touch()} WHERE id=?", (*fields.values(), user_id)
    )


def profile_complete(user: dict) -> bool:
    return bool(user["name"] and user["phone"] and user["address"])


async def list_pets(user_id: int) -> list[dict]:
    return await db.fetch_all(
        "SELECT * FROM pets WHERE user_id=? AND is_active=1 ORDER BY id", (user_id,)
    )


async def get_pet(pet_id: int | None) -> dict | None:
    if not pet_id:
        return None
    return await db.fetch_one("SELECT * FROM pets WHERE id=?", (pet_id,))


async def add_pet(user_id: int, name: str, species: str, breed: str | None,
                  size: str, notes: str | None, traits: str | None = None) -> int:
    return await db.execute(
        "INSERT INTO pets (user_id, name, species, breed, size, notes, traits) VALUES (?,?,?,?,?,?,?)",
        (user_id, name, species, breed, size, notes, traits or None),
    )


async def update_pet(pet_id: int, user_id: int, name: str, species: str, breed: str | None,
                     size: str, notes: str | None, traits: str | None = None) -> bool:
    """Меняет питомца только если он принадлежит этому клиенту."""
    cur = await db.fetch_one("SELECT id FROM pets WHERE id=? AND user_id=? AND is_active=1", (pet_id, user_id))
    if not cur:
        return False
    await db.execute(
        f"UPDATE pets SET name=?, species=?, breed=?, size=?, notes=?, traits=?, {db.touch()} WHERE id=?",
        (name, species, breed, size, notes, traits or None, pet_id))
    return True


async def deactivate_pet(pet_id: int, user_id: int) -> bool:
    """Убирает питомца из списка клиента (в старых заказах он остаётся)."""
    cur = await db.fetch_one("SELECT id FROM pets WHERE id=? AND user_id=? AND is_active=1", (pet_id, user_id))
    if not cur:
        return False
    await db.execute(f"UPDATE pets SET is_active=0, {db.touch()} WHERE id=?", (pet_id,))
    return True


_VISIBLE = "(o.user_id=? AND o.created_at > COALESCE((SELECT erased_at FROM users WHERE id=o.user_id), ''))"


async def last_order(user_id: int) -> dict | None:
    """Последний заказ клиента; всё, что было до удаления его данных, он больше не видит."""
    return await db.fetch_one(
        f"SELECT o.* FROM orders o WHERE {_VISIBLE} ORDER BY o.id DESC LIMIT 1", (user_id,))


async def user_orders(user_id: int, limit: int = 8) -> list[dict]:
    """Заказы для экрана «Мои заказы»: услуга, статус, питомец; только после удаления данных (если оно было)."""
    return await db.fetch_all(
        f"""SELECT o.id, o.status, o.start_at, o.price_total, o.match_tier, o.created_at, s.name, p.name AS pet_name
            FROM orders o JOIN services s ON s.id=o.service_id LEFT JOIN pets p ON p.id=o.pet_id
            WHERE {_VISIBLE} ORDER BY o.id DESC LIMIT ?""", (user_id, limit))


async def find_profile_by_phone(phone: str, exclude_id: int) -> dict | None:
    """Профиль другого Telegram-аккаунта с тем же номером (только заполненный и не удалённый)."""
    return await db.fetch_one(
        "SELECT * FROM users WHERE phone=? AND id<>? AND name IS NOT NULL AND address IS NOT NULL "
        "ORDER BY id DESC LIMIT 1", (phone, exclude_id))


async def copy_profile(from_id: int, to_id: int) -> None:
    """Переносит в новый аккаунт имя, телефон, адрес, способ связи и копии питомцев; старый аккаунт не трогаем."""
    src = await get_user(from_id)
    async with db.transaction() as conn:
        await conn.execute(
            f"UPDATE users SET name=?, phone=?, district=?, address=?, address_verified=?, outside_mkad=?, "
            f"contact_pref=?, {db.touch()} WHERE id=?",
            (src["name"], src["phone"], src["district"], src["address"], src["address_verified"],
             src["outside_mkad"], src["contact_pref"], to_id))
        await conn.execute(
            "INSERT INTO pets (user_id, name, species, breed, size, notes, traits) "
            "SELECT ?, name, species, breed, size, notes, traits FROM pets WHERE user_id=? AND is_active=1",
            (to_id, from_id))


async def active_order_count(user_id: int) -> int:
    from orders import ACTIVE_STATUSES
    row = await db.fetch_one(
        f"SELECT COUNT(*) AS n FROM orders WHERE user_id=? AND status IN ({','.join('?' * len(ACTIVE_STATUSES))})",
        (user_id, *ACTIVE_STATUSES))
    return row["n"]


async def erase_user(user_id: int) -> None:
    """Стирает личные данные клиента по его просьбе. Строка users остаётся (на неё ссылаются заказы), но пустая;
    заказы остаются для учёта без адресов, текст отзывов стирается, в журнал согласий пишется отзыв согласия."""
    user = await get_user(user_id)
    if user is None:
        return
    async with db.transaction() as conn:
        await conn.execute(
            f"UPDATE users SET name=NULL, phone=NULL, district=NULL, address=NULL, tg_username=NULL, "
            f"address_verified=0, lead_stage=0, pdn_consent=0, marketing_consent=0, contact_pref='telegram', "
            f"erased_at=datetime('now'), {db.touch()} WHERE id=?", (user_id,))
        await conn.execute(
            f"UPDATE pets SET name='удалено', breed=NULL, notes=NULL, traits=NULL, is_active=0, {db.touch()} "
            "WHERE user_id=?", (user_id,))
        await conn.execute(f"UPDATE orders SET address=NULL, address_to=NULL, {db.touch()} WHERE user_id=?", (user_id,))
        await conn.execute(f"UPDATE reviews SET text=NULL, {db.touch()} WHERE user_id=?", (user_id,))
        await conn.execute(
            "INSERT INTO consents (user_id, tg_id, type, granted, text_version) VALUES (?,?,?,?,?)",
            (user_id, user["tg_id"], "pdn", 0, "erased"))


_TRACES = (("messages", "party_tg_id"), ("web_sessions", "uid"), ("web_events", "uid"), ("web_files", "uid"),
           ("push_subs", "uid"), ("passkeys", "uid"), ("web_login", "tg_id"), ("web_links", "web_uid"),
           ("login_codes", "tg_id"), ("web_requests", "web_uid"), ("rooms", "client_tg_id"))


async def delete_client(user_id: int, admin_tg_id: int | None = None) -> tuple[str, str]:
    """Удаление клиента из админки. Возвращает (код, сообщение): deleted — стёрт целиком вместе с заказами, перепиской и
    входами; erased — были оплаты, учёт сохранён, стёрты только личные данные; blocked — нельзя (админ, исполнитель,
    незавершённые заказы); missing — такого клиента нет. Всё в одной транзакции, в журнал админа — без личных данных."""
    from config import config
    from orders import ACTIVE_STATUSES
    user = await get_user(user_id)
    if user is None:
        return "missing", "Клиент не найден: возможно, его уже удалили."
    tg = user["tg_id"]
    if tg in config.admin_ids:
        return "blocked", "Это администратор: удалить его нельзя."
    if await db.fetch_one("SELECT 1 AS x FROM executors WHERE tg_id=?", (tg,)):
        return "blocked", "Этот клиент ещё и исполнитель. Сначала удалите его в разделе «Исполнители»."
    marks = ",".join("?" * len(ACTIVE_STATUSES))
    live = await db.fetch_all(f"SELECT id FROM orders WHERE user_id=? AND status IN ({marks}) ORDER BY id", (user_id, *ACTIVE_STATUSES))
    if live:
        ids = ", ".join(f"№{o['id']}" for o in live)
        return "blocked", f"У клиента есть незавершённые заказы: {ids}. Сначала завершите или отмените их."
    paid = await db.fetch_one("SELECT 1 AS x FROM payments p JOIN orders o ON o.id=p.order_id "
                              "WHERE o.user_id=? AND p.status IN ('paid','refunded')", (user_id,))
    if paid:
        await erase_user(user_id)  # оплаты нужны для учёта: заказы остаются, но без личных данных
    async with db.transaction() as conn:
        for table, col in _TRACES:
            await conn.execute(f"DELETE FROM {table} WHERE {col}=?", (tg,))
        if not paid:
            mine = "SELECT id FROM orders WHERE user_id=?"
            for table in ("order_statuses_log", "payments", "executor_payouts", "reviews", "reminders", "rooms"):
                await conn.execute(f"DELETE FROM {table} WHERE order_id IN ({mine})", (user_id,))
            await conn.execute("DELETE FROM orders WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM reviews WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM pets WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM consents WHERE user_id=?", (user_id,))
            await conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        if admin_tg_id is not None:
            await conn.execute("INSERT INTO admin_log (admin_tg_id, command, args) VALUES (?,?,?)",
                               (admin_tg_id, "client_delete", f"id={user_id} {'erased' if paid else 'deleted'}"))
    if paid:
        return "erased", "У клиента были оплаченные заказы: они сохранены для учёта, а личные данные и переписка стёрты."
    return "deleted", "Клиент удалён вместе с заказами, заявками и перепиской."


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-zа-я0-9]+", (text or "").lower().replace("ё", "е"))


async def match_second_factor(old_id: int, text: str) -> bool:
    """Второй признак «это вы» после совпавшего телефона: кличка питомца или адрес (улица и дом) из прошлого профиля.
    Адрес засчитываем, только если названы и слово (≥4 букв), и номер дома, и всё это есть в старом адресе:
    «Химки» или «кв 33» по отдельности ничего не подтверждают."""
    toks = _tokens(text)
    if not toks:
        return False
    for p in await list_pets(old_id):
        if toks == _tokens(p["name"]):
            return True
    user = await get_user(old_id)
    have = set(_tokens(user["address"] if user else ""))
    word = any(len(t) >= 4 and t.isalpha() for t in toks)
    digit = any(t.isdigit() for t in toks)
    return word and digit and all(t in have for t in toks if t not in {"ул", "улица", "д", "дом"})
