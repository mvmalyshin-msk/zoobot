"""Ожидающие входы на сайт и одноразовые коды — в базе, а не в памяти процесса.

Раньше они жили в словарях: перезапуск или пересборка на хостинге стирали их, и клиент получал «Код не подошёл»,
а «Войти через Telegram» зависало на «Ждём подтверждения…». Теперь запись переживает перезапуск и сама
истекает по сроку. Вид (kind) разделяет записи: 'tg' — вход через Telegram (ключ — хеш токена),
'ph' — код по телефону, 'em' — код по почте."""
import json
import time

import db


def _k(kind: str, key: str) -> str:
    return f"{kind}:{key}"


async def put(kind: str, key: str, data: dict, ttl: int) -> None:
    now = time.time()
    await db.execute("DELETE FROM pending_logins WHERE exp < ?", (now,))
    await db.execute("INSERT OR REPLACE INTO pending_logins (k, data, exp) VALUES (?, ?, ?)",
                     (_k(kind, key), json.dumps(data, ensure_ascii=False), now + ttl))


async def get(kind: str, key: str) -> dict | None:
    """Запись или None, если её нет или срок вышел."""
    row = await db.fetch_one("SELECT data, exp FROM pending_logins WHERE k=?", (_k(kind, key),))
    if not row or row["exp"] < time.time():
        return None
    return json.loads(row["data"])


async def update(kind: str, key: str, data: dict) -> None:
    await db.execute("UPDATE pending_logins SET data=? WHERE k=?", (json.dumps(data, ensure_ascii=False), _k(kind, key)))


async def take(kind: str, key: str) -> dict | None:
    """Забирает запись один раз: второй вызов вернёт None (вход и код одноразовые)."""
    async with db.transaction() as conn:
        cur = await conn.execute("SELECT data, exp FROM pending_logins WHERE k=?", (_k(kind, key),))
        row = await cur.fetchone()
        if not row:
            return None
        await conn.execute("DELETE FROM pending_logins WHERE k=?", (_k(kind, key),))
    data, exp = row[0], row[1]
    return json.loads(data) if exp >= time.time() else None


async def drop(kind: str, key: str) -> None:
    await db.execute("DELETE FROM pending_logins WHERE k=?", (_k(kind, key),))
