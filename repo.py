"""Операции с клиентами, согласиями и питомцами."""
import db

USER_FIELDS = {"name", "phone", "district", "address", "outside_mkad", "address_verified"}


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


async def last_order(user_id: int) -> dict | None:
    return await db.fetch_one(
        "SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)
    )
