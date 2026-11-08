"""Слой доступа к SQLite (aiosqlite) + простые миграции.

Принцип: schema.sql = версия 1. Любое следующее изменение схемы — новая запись
в MIGRATIONS (номер версии → SQL). При старте бот применяет всё, чего ещё нет.
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from config import config

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).parent / "schema.sql"

# Будущие миграции добавляем сюда. Пример:
# MIGRATIONS = {2: "ALTER TABLE orders ADD COLUMN comment TEXT;"}
MIGRATIONS: dict[int, str] = {
    # 2: проверка адреса по карте. outside_mkad у клиента — «адрес из профиля за МКАД»;
    #    address_unverified у заказа — карта была недоступна или клиент оставил адрес как написал.
    2: """
    ALTER TABLE users ADD COLUMN outside_mkad INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN address_verified INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE orders ADD COLUMN address_unverified INTEGER NOT NULL DEFAULT 0;
    """,
    # 3: праздничные дни из коробки (иначе надбавка за праздники не работала, пока админ не внесёт даты).
    #    Список правится командами /holiday_add и /holiday_del.
    # 5: стадия лида: 1 — оставил телефон, 2 — оставил адрес (чтобы админу не слать одно и то же дважды)
    5: "ALTER TABLE users ADD COLUMN lead_stage INTEGER NOT NULL DEFAULT 0;",
    # 6: когда клиент удалил данные (старые заказы ему больше не показываем), способ связи, комментарий к заказу
    6: """
    ALTER TABLE users ADD COLUMN erased_at TEXT;
    ALTER TABLE users ADD COLUMN contact_pref TEXT NOT NULL DEFAULT 'telegram';
    ALTER TABLE orders ADD COLUMN comment TEXT;
    """,
    # 7: служебные значения (какое обновление уже объявлено админам)
    7: "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);",
    # 8: заявки с сайта (клиенты без Telegram); с клиентом связывается админ по телефону
    8: """
    CREATE TABLE IF NOT EXISTS web_requests (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        service_id   INTEGER REFERENCES services(id),
        name         TEXT NOT NULL,
        phone        TEXT NOT NULL,
        contact_pref TEXT NOT NULL DEFAULT 'call',
        address      TEXT NOT NULL,
        when_text    TEXT,
        pet          TEXT,
        comment      TEXT,
        status       TEXT NOT NULL DEFAULT 'new',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 9: веб-версия бота (/chat): лента событий для браузера, сессии, одноразовые ссылки входа админа, файлы
    9: """
    CREATE TABLE IF NOT EXISTS web_sessions (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT NOT NULL UNIQUE,
        uid        INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS web_events (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        op         TEXT NOT NULL,
        mid        INTEGER,
        payload    TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_web_events_uid ON web_events(uid, id);
    CREATE TABLE IF NOT EXISTS web_login (
        token_hash TEXT PRIMARY KEY,
        tg_id      INTEGER NOT NULL,
        used       INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS web_files (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        name       TEXT NOT NULL,
        data       BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 10: зоотакси можно заказать для другого человека: его имя и телефон для связи
    10: """
    ALTER TABLE orders ADD COLUMN rider_name TEXT;
    ALTER TABLE orders ADD COLUMN rider_phone TEXT;
    """,
    # 11: портал администратора: переписка с клиентами и исполнителями, общий чат по заказу,
    #     коды входа (Telegram/почта), привязка веб-клиента к Telegram для проверки
    11: """
    CREATE TABLE IF NOT EXISTS messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        party_tg_id INTEGER NOT NULL,
        party_kind  TEXT NOT NULL,
        direction   TEXT NOT NULL,
        text        TEXT NOT NULL,
        order_id    INTEGER,
        author      TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_messages_party ON messages(party_tg_id, id);
    CREATE TABLE IF NOT EXISTS rooms (
        order_id       INTEGER PRIMARY KEY,
        client_tg_id   INTEGER NOT NULL,
        executor_tg_id INTEGER NOT NULL,
        open           INTEGER NOT NULL DEFAULT 1,
        created_at     TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS login_codes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id      INTEGER NOT NULL,
        code_hash  TEXT NOT NULL,
        via        TEXT NOT NULL,
        attempts   INTEGER NOT NULL DEFAULT 0,
        used       INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS web_links (
        token_hash TEXT PRIMARY KEY,
        web_uid    INTEGER NOT NULL,
        tg_id      INTEGER,
        used       INTEGER NOT NULL DEFAULT 0,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    ALTER TABLE users ADD COLUMN verified_tg_id INTEGER;
    ALTER TABLE web_requests ADD COLUMN admin_note TEXT;
    """,
    # 12: чем подтверждён телефон клиента: 'telegram' (номер со своей кнопки), 'yandex' (Яндекс ID), пусто — не подтверждён
    12: "ALTER TABLE users ADD COLUMN phone_verified TEXT;",
    # 13: журнал заказа хранит формулу цены на момент создания (см. orders.price_note)
    13: "ALTER TABLE order_statuses_log ADD COLUMN note TEXT;",
    # 15: подписки браузеров на уведомления (Web Push, см. webpush.py)
    15: """
    CREATE TABLE IF NOT EXISTS push_subs (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        endpoint   TEXT NOT NULL UNIQUE,
        p256dh     TEXT NOT NULL,
        auth       TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_push_subs_uid ON push_subs(uid);
    """,
    # 16: доверенные устройства: вход запоминает название устройства (см. webchat.device_label), в портале его можно отозвать
    16: "ALTER TABLE web_sessions ADD COLUMN device TEXT;",
    # 17: зоотакси по формуле Яндекс Такси («Эконом», Москва): цена услуги — посадка 189 ₽, дальше км и минуты
    #     по маршруту (taxi.py). Меняем только старую стартовую цену 1200 — цену, заданную админом, не трогаем.
    17: "UPDATE services SET base_price=189 WHERE code='taxi' AND base_price=1200;",
    # 18: ключи доступа (Face ID / Touch ID) администратора, см. passkeys.py: открытый ключ, счётчик, название устройства
    18: """
    CREATE TABLE IF NOT EXISTS passkeys (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        uid        INTEGER NOT NULL,
        cred_id    TEXT NOT NULL UNIQUE,
        public_key TEXT NOT NULL,
        sign_count INTEGER NOT NULL DEFAULT 0,
        device     TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        last_used  TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_passkeys_uid ON passkeys(uid);
    """,
    # 19: общая наценка сервиса (в процентах, по умолчанию 0 — цены не меняются); правится на странице «Тарифы» портала
    19: """
INSERT INTO price_rules (service_id, name, condition_type, effect_type, value, sort_order)
SELECT NULL, 'Наценка сервиса', 'always', 'percent', 0, 5
WHERE NOT EXISTS (SELECT 1 FROM price_rules WHERE condition_type='always');
""",
    # 20: заявка с сайта помнит гостя (чат с админом), устройство и IP; сессия — последний IP и время визита
    20: """
    ALTER TABLE web_requests ADD COLUMN web_uid INTEGER;
    ALTER TABLE web_requests ADD COLUMN ip TEXT;
    ALTER TABLE web_requests ADD COLUMN device TEXT;
    ALTER TABLE web_sessions ADD COLUMN ip TEXT;
    ALTER TABLE web_sessions ADD COLUMN seen_at TEXT;
    """,
    # 21: методика цен (ноябрь 2026): база — рыночная цена исполнителя (Dogsy, Яндекс Услуги, «Наша няня»), клиент платит
    #     базу + наценку сервиса 30 %. Меняем только прежние стартовые значения — цены, заданные админом, не трогаем.
    21: """
UPDATE services SET base_price=700  WHERE code='walk30'         AND base_price=400;
UPDATE services SET base_price=1000 WHERE code='walk60'         AND base_price=700;
UPDATE services SET base_price=800  WHERE code='sitter_visit'   AND base_price=600;
UPDATE services SET base_price=3000 WHERE code='sitter_live'    AND base_price=2500;
UPDATE services SET base_price=1600 WHERE code='boarding_hotel' AND base_price=1800;
UPDATE price_rules SET effect_type='percent', value=20  WHERE condition_type='pet_size_l'   AND effect_type='fixed' AND value=100 AND service_id IS NULL;
UPDATE price_rules SET value=300 WHERE condition_type='urgent_today' AND effect_type='fixed' AND value=200 AND service_id IS NULL;
UPDATE price_rules SET value=30  WHERE condition_type='always' AND effect_type='percent' AND value=0 AND service_id IS NULL;
""",
    # 22: собственная аналитика посещений сайта и бота (см. analytics.py): события, без имён и текстов сообщений
    22: """
CREATE TABLE IF NOT EXISTS analytics_events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL DEFAULT (datetime('now')),
    source  TEXT NOT NULL,          -- 'web' | 'bot'
    vid     TEXT NOT NULL,          -- посетитель: случайный код из браузера или tg:<id>
    sid     TEXT NOT NULL,          -- сессия (до 30 минут тишины)
    type    TEXT NOT NULL,          -- web: pv click scroll ping leave focus submit; bot: cmd btn msg
    path    TEXT, target TEXT, value INTEGER, state TEXT,
    device  TEXT, browser TEXT, os TEXT, ref TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_an_src_ts ON analytics_events(source, ts);
CREATE INDEX IF NOT EXISTS idx_an_sid ON analytics_events(sid);
CREATE INDEX IF NOT EXISTS idx_an_vid ON analytics_events(vid, ts);
""",
    # 14: шаги диалога (FSM) — в базе, чтобы переживать перезапуск бота (см. fsm_store.py)
    14: """
    CREATE TABLE IF NOT EXISTS fsm (
        key TEXT PRIMARY KEY,
        state TEXT,
        data TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    # 4: повадки питомца (анкета для выгульщика): коды через запятую, см. traits.py
    4: "ALTER TABLE pets ADD COLUMN traits TEXT;",
    3: """
    INSERT OR IGNORE INTO holidays (day, title) VALUES
        ('2026-11-04','День народного единства'),
        ('2026-12-31','Новогодняя ночь'),
        ('2027-01-01','Новогодние каникулы'),
        ('2027-01-02','Новогодние каникулы'),
        ('2027-01-03','Новогодние каникулы'),
        ('2027-01-04','Новогодние каникулы'),
        ('2027-01-05','Новогодние каникулы'),
        ('2027-01-06','Новогодние каникулы'),
        ('2027-01-07','Рождество'),
        ('2027-01-08','Новогодние каникулы'),
        ('2027-02-23','День защитника Отечества'),
        ('2027-03-08','Международный женский день'),
        ('2027-05-01','Праздник весны и труда'),
        ('2027-05-09','День Победы'),
        ('2027-06-12','День России'),
        ('2027-11-04','День народного единства'),
        ('2027-12-31','Новогодняя ночь');
    """,
}


async def get_conn() -> aiosqlite.Connection:
    """Открыть соединение. Row-фабрика позволяет обращаться к колонкам по имени."""
    conn = await aiosqlite.connect(config.db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA journal_mode = WAL")  # устойчивее к параллельным записям
    return conn


async def current_version(conn: aiosqlite.Connection) -> int:
    """Текущая версия схемы (0 — БД пустая)."""
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    if not await cur.fetchone():
        return 0
    cur = await conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_version")
    row = await cur.fetchone()
    return row["v"]


async def init_db() -> None:
    """Создать БД и применить недостающие миграции. Вызывается при старте бота."""
    conn = await get_conn()
    try:
        version = await current_version(conn)

        if version == 0:
            log.info("Создаю БД из schema.sql (версия 1)")
            await conn.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
            await conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            await conn.commit()
            version = 1

        for v in sorted(MIGRATIONS):
            if v > version:
                log.info("Применяю миграцию %s", v)
                await conn.executescript(MIGRATIONS[v])
                await conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
                await conn.commit()
    finally:
        await conn.close()


# ---------- Небольшие хелперы, чтобы не плодить boilerplate в хендлерах ----------

async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    conn = await get_conn()
    try:
        cur = await conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await conn.close()


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    conn = await get_conn()
    try:
        cur = await conn.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await conn.close()


async def execute(sql: str, params: tuple = ()) -> int:
    """Выполнить запись. Возвращает lastrowid.
    updated_at обновляем явно в SQL там, где это UPDATE (см. touch())."""
    conn = await get_conn()
    try:
        cur = await conn.execute(sql, params)
        await conn.commit()
        return cur.lastrowid
    finally:
        await conn.close()


@asynccontextmanager
async def transaction():
    """Несколько запросов как одно целое: либо все применились, либо ни один.
    Использование:  async with db.transaction() as conn:  await conn.execute(...)"""
    conn = await get_conn()
    try:
        await conn.execute("BEGIN")
        yield conn
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise
    finally:
        await conn.close()


def touch() -> str:
    """Фрагмент SQL для обновления updated_at: UPDATE t SET x=?, {touch()} WHERE ..."""
    return "updated_at = datetime('now')"
