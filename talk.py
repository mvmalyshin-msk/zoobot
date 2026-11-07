"""Переписка через бота. Всё, что клиент или исполнитель пишет вне сценария, попадает админу и в портал;
ответы админа уходят от имени бота. Общий чат по заказу («комната»): пока он открыт, сообщения клиента
пересылаются исполнителю и обратно, а админ видит переписку в портале."""
import logging

import db
import keyboards as kb
import orders
from config import config

log = logging.getLogger(__name__)
MAX_TEXT = 2000


async def save(party_tg_id: int, kind: str, direction: str, text: str, order_id: int | None = None,
               author: str | None = None) -> int:
    return await db.execute(
        "INSERT INTO messages (party_tg_id, party_kind, direction, text, order_id, author) VALUES (?,?,?,?,?,?)",
        (party_tg_id, kind, direction, text[:MAX_TEXT], order_id, author))


async def party(tg_id: int) -> tuple[str, str]:
    """Кто пишет: ('executor', имя) или ('client', имя)."""
    ex = await db.fetch_one("SELECT name FROM executors WHERE tg_id=?", (tg_id,))
    if ex:
        return "executor", ex["name"]
    u = await db.fetch_one("SELECT name FROM users WHERE tg_id=?", (tg_id,))
    return "client", (u["name"] if u and u["name"] else ("гость сайта" if tg_id < 0 else "без имени"))


async def room_for(tg_id: int) -> dict | None:
    """Открытый общий чат по незакрытому заказу (после выполнения или отмены пересылка прекращается сама)."""
    return await db.fetch_one(
        """SELECT r.* FROM rooms r JOIN orders o ON o.id=r.order_id
           WHERE r.open=1 AND o.status NOT IN ('completed','cancelled') AND (r.client_tg_id=? OR r.executor_tg_id=?)
           ORDER BY r.updated_at DESC LIMIT 1""", (tg_id, tg_id))


async def incoming(bot, tg_id: int, text: str) -> str:
    """Сообщение вне сценария. Возвращает 'room' (переслано в общий чат) или 'admin' (передано админу)."""
    text = text.strip()[:MAX_TEXT]
    kind, name = await party(tg_id)
    room = await room_for(tg_id)
    if room:
        to = room["executor_tg_id"] if tg_id == room["client_tg_id"] else room["client_tg_id"]
        who = "Клиент" if tg_id == room["client_tg_id"] else f"Исполнитель {name}"
        await save(tg_id, kind, "room", text, room["order_id"])
        await orders.notify(bot, to, f"💬 {who} (заказ №{room['order_id']}):\n{text}")
        return "room"
    await save(tg_id, kind, "in", text)
    head = f"✉️ Исполнитель {name}" if kind == "executor" else f"✉️ Клиент {name}"
    await orders.notify_admins(bot, f"{head}:\n{text}", kb.reply_party(tg_id, portal_url()))
    return "admin"


def portal_url(path: str = "") -> str | None:
    return f"{config.webapp_url}/portal{path}" if config.webapp_url else None


async def admin_say(bot, tg_id: int, text: str, author: str) -> bool:
    text = text.strip()[:MAX_TEXT]
    kind, _ = await party(tg_id)
    ok = await orders.notify(bot, tg_id, "👤 Администратор:\n" + text)
    await save(tg_id, kind, "out", text, author=author)
    return ok


async def open_room(bot, order_id: int) -> tuple[bool, str]:
    o = await orders.get_order_full(order_id)
    if not o:
        return False, "Заказ не найден."
    if not o.get("executor_tg_id"):
        return False, "Сначала назначьте исполнителя с Telegram — иначе ему некуда пересылать сообщения."
    if o["status"] in ("completed", "cancelled"):
        return False, "Заказ уже закрыт."
    await db.execute(
        f"""INSERT INTO rooms (order_id, client_tg_id, executor_tg_id, open) VALUES (?,?,?,1)
            ON CONFLICT(order_id) DO UPDATE SET open=1, client_tg_id=excluded.client_tg_id,
            executor_tg_id=excluded.executor_tg_id, {db.touch()}""",
        (order_id, o["client_tg_id"], o["executor_tg_id"]))
    await orders.notify(bot, o["client_tg_id"],
                        f"💬 Открыт чат с исполнителем по заказу №{order_id} ({o['executor_name']}). "
                        "Пишите сюда — сообщения получит исполнитель, администратор тоже их видит.")
    await orders.notify(bot, o["executor_tg_id"],
                        f"💬 Открыт чат с клиентом по заказу №{order_id} ({o['client_name']}). "
                        "Пишите сюда — сообщения получит клиент, администратор тоже их видит.")
    return True, f"Чат клиента и исполнителя по заказу №{order_id} открыт."


async def close_room(bot, order_id: int) -> tuple[bool, str]:
    room = await db.fetch_one("SELECT * FROM rooms WHERE order_id=? AND open=1", (order_id,))
    if not room:
        return False, "Чат уже закрыт."
    await db.execute(f"UPDATE rooms SET open=0, {db.touch()} WHERE order_id=?", (order_id,))
    for tg in (room["client_tg_id"], room["executor_tg_id"]):
        await orders.notify(bot, tg, f"Чат по заказу №{order_id} закрыт. Вопросы — администратору, просто напишите сюда.")
    return True, f"Чат по заказу №{order_id} закрыт."


async def room_log(order_id: int) -> list[dict]:
    return await db.fetch_all("SELECT * FROM messages WHERE order_id=? AND direction='room' ORDER BY id", (order_id,))


async def thread(tg_id: int, after: int = 0) -> list[dict]:
    return await db.fetch_all("SELECT * FROM messages WHERE party_tg_id=? AND id>? ORDER BY id LIMIT 500", (tg_id, after))
