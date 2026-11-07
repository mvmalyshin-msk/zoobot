"""Хранилище шагов диалога (FSM aiogram) в SQLite на томе /data.

Раньше шаги жили в памяти (MemoryStorage): после каждой выкладки бот «забывал», что человек
посреди регистрации, и адрес с карты или геопозиция приходили «никуда» — бот молчал.
Теперь шаг и его данные лежат в таблице fsm и переживают перезапуск."""
import json
from datetime import date, datetime
from typing import Any, Mapping

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db


def _key(k: StorageKey) -> str:
    parts = (k.bot_id, k.chat_id, k.user_id, k.thread_id, k.business_connection_id, k.destiny)
    return ":".join("" if p is None else str(p) for p in parts)


def _encode(o: Any) -> Any:
    if isinstance(o, datetime):
        return {"__datetime__": o.isoformat()}
    if isinstance(o, date):
        return {"__date__": o.isoformat()}
    if isinstance(o, (set, frozenset)):
        return list(o)
    raise TypeError(f"Нельзя сохранить в шаге диалога: {type(o).__name__}")


def _decode(d: dict) -> Any:
    if len(d) == 1 and "__datetime__" in d:
        return datetime.fromisoformat(d["__datetime__"])
    if len(d) == 1 and "__date__" in d:
        return date.fromisoformat(d["__date__"])
    return d


class SQLiteStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await db.execute(
            f"INSERT INTO fsm (key, state) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET state=excluded.state, {db.touch()}",
            (_key(key), value))

    async def get_state(self, key: StorageKey) -> str | None:
        row = await db.fetch_one("SELECT state FROM fsm WHERE key=?", (_key(key),))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        raw = json.dumps(dict(data), ensure_ascii=False, default=_encode)
        await db.execute(
            f"INSERT INTO fsm (key, data) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET data=excluded.data, {db.touch()}",
            (_key(key), raw))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await db.fetch_one("SELECT data FROM fsm WHERE key=?", (_key(key),))
        return json.loads(row["data"], object_hook=_decode) if row and row["data"] else {}

    async def close(self) -> None:
        pass
