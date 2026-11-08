-- ============================================================
-- Зооконсьерж: схема БД, версия 1
-- Применяется db.py при первом запуске. Дальнейшие изменения схемы
-- добавляются как новые миграции в db.py (MIGRATIONS), а не правкой этого файла.
-- Время хранится в UTC в формате ISO-8601 (TEXT), в Москву переводим в коде.
-- ============================================================

PRAGMA foreign_keys = ON;

-- Служебная таблица версий схемы
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Клиенты ----------
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id             INTEGER NOT NULL UNIQUE,          -- Telegram user_id
    tg_username       TEXT,
    name              TEXT,
    phone             TEXT,
    district          TEXT,                             -- район
    address           TEXT,                             -- адрес (по умолчанию для заказов)
    pdn_consent       INTEGER NOT NULL DEFAULT 0,       -- кэш: 1 = согласие на ПДн дано
    marketing_consent INTEGER NOT NULL DEFAULT 0,       -- кэш: 1 = согласие на рассылки (НЕ предзаполнено)
    is_blocked        INTEGER NOT NULL DEFAULT 0,       -- 1 = бот заблокирован пользователем
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Журнал согласий: каждая выдача/отзыв — отдельная строка (юридическая история)
CREATE TABLE IF NOT EXISTS consents (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    tg_id      INTEGER NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('pdn', 'marketing')),
    granted    INTEGER NOT NULL,                        -- 1 = дано, 0 = отозвано
    text_version TEXT NOT NULL,                         -- версия текста согласия (texts.PDN_VERSION)
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_consents_user ON consents(user_id, type);

-- ---------- Питомцы ----------
CREATE TABLE IF NOT EXISTS pets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    name       TEXT NOT NULL,                           -- кличка
    species    TEXT NOT NULL,                           -- вид: собака / кошка / другое
    breed      TEXT,
    size       TEXT CHECK (size IN ('S', 'M', 'L')),
    notes      TEXT,                                    -- агрессия, здоровье, аллергии
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pets_user ON pets(user_id);

-- ---------- Услуги и цены ----------
-- category — одна из 4 услуг, а строка services — конкретный вариант (30 мин, 60 мин и т.д.)
CREATE TABLE IF NOT EXISTS services (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,                    -- walk30, walk60, ...
    category   TEXT NOT NULL CHECK (category IN ('walk', 'sitter', 'boarding', 'taxi')),
    name       TEXT NOT NULL,
    base_price INTEGER NOT NULL,                        -- в рублях
    unit       TEXT NOT NULL,                           -- 'выгул' / 'час' / 'сутки' / 'поездка'
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Правила цены. Меняются админ-командами /rule_add, /rule_del без правки кода.
--   condition_type: pet_size_l | night | urgent_today | outside_mkad | holiday
--   effect_type:    fixed (+N ₽) | percent (+N % от текущей суммы)
--   service_id NULL = правило действует на все услуги
CREATE TABLE IF NOT EXISTS price_rules (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id     INTEGER REFERENCES services(id),
    name           TEXT NOT NULL,                       -- подпись в разбивке для клиента
    condition_type TEXT NOT NULL,
    effect_type    TEXT NOT NULL CHECK (effect_type IN ('fixed', 'percent')),
    value          REAL NOT NULL,
    sort_order     INTEGER NOT NULL DEFAULT 100,        -- порядок применения (fixed раньше percent)
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Праздничные дни (для правила holiday), админ добавляет даты сам
CREATE TABLE IF NOT EXISTS holidays (
    day        TEXT PRIMARY KEY,                        -- YYYY-MM-DD
    title      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Исполнители ----------
CREATE TABLE IF NOT EXISTS executors (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tg_id             INTEGER UNIQUE,                   -- для уведомлений исполнителю
    name              TEXT NOT NULL,
    phone             TEXT,
    legal_status      TEXT NOT NULL CHECK (legal_status IN ('self_employed', 'ip', 'legal', 'individual')),
    services          TEXT NOT NULL DEFAULT '',         -- категории через запятую: walk,sitter
    districts         TEXT NOT NULL DEFAULT '',         -- районы через запятую
    rate_type         TEXT NOT NULL DEFAULT 'percent' CHECK (rate_type IN ('percent', 'fixed')),
    rate_value        REAL NOT NULL DEFAULT 70,         -- % от цены клиента или фикс ₽ за заказ
    birth_year        INTEGER,                          -- нужен для физлиц 14–18
    parent_consent    INTEGER NOT NULL DEFAULT 0,       -- согласие родителей (для 14–18)
    payout_details    TEXT,                             -- реквизиты для выплат (минимум, только нужное)
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Заказы ----------
-- status: created → confirmed → paid → assigned → in_progress → completed | cancelled
CREATE TABLE IF NOT EXISTS orders (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    pet_id           INTEGER REFERENCES pets(id),
    service_id       INTEGER NOT NULL REFERENCES services(id),
    status           TEXT NOT NULL DEFAULT 'created'
                     CHECK (status IN ('created','confirmed','paid','assigned','in_progress','completed','cancelled')),
    start_at         TEXT NOT NULL,                     -- начало слота, UTC ISO
    end_at           TEXT,                              -- конец (для суток/часов)
    quantity         REAL NOT NULL DEFAULT 1,           -- часов / суток / 1 для выгула и такси
    address          TEXT,                              -- адрес услуги / точка А для такси
    address_to       TEXT,                              -- точка Б (только такси)
    is_urgent        INTEGER NOT NULL DEFAULT 0,        -- «сегодня»
    outside_mkad     INTEGER NOT NULL DEFAULT 0,
    params_json      TEXT,                              -- доп. параметры услуги (приходящая/с проживанием и т.д.)
    price_total      INTEGER NOT NULL DEFAULT 0,        -- итог клиенту, ₽
    price_breakdown  TEXT,                              -- JSON-разбивка, зафиксированная на момент заказа
    executor_id      INTEGER REFERENCES executors(id),
    executor_pay     INTEGER,                           -- вознаграждение исполнителя, ₽
    margin           INTEGER,                           -- price_total - executor_pay
    cancel_reason    TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_start  ON orders(start_at);

-- История переходов статусов (аудит + основа для уведомлений)
CREATE TABLE IF NOT EXISTS order_statuses_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,                          -- 'client:<tg_id>' / 'admin:<tg_id>' / 'system'
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_status_log_order ON order_statuses_log(order_id);

-- ---------- Платежи ----------
-- status: pending → link_sent → paid → refunded
CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     INTEGER NOT NULL REFERENCES orders(id),
    amount       INTEGER NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','link_sent','paid','refunded')),
    payment_link TEXT,
    link_sent_at TEXT,
    paid_at      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);

-- ---------- Выплаты исполнителям ----------
CREATE TABLE IF NOT EXISTS executor_payouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    executor_id INTEGER NOT NULL REFERENCES executors(id),
    order_id    INTEGER NOT NULL REFERENCES orders(id),
    amount      INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid')),
    receipt_url TEXT,                                   -- чек самозанятого
    paid_at     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Отзывы ----------
CREATE TABLE IF NOT EXISTS reviews (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    executor_id INTEGER REFERENCES executors(id),
    rating      INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    text        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- Напоминания ----------
-- kind: client_24h | client_2h | pay_3h | executor_2h | review_request
CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id),
    kind       TEXT NOT NULL,
    send_at    TEXT NOT NULL,                           -- UTC ISO
    sent       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (order_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(sent, send_at);

-- ---------- Журнал действий админа ----------
CREATE TABLE IF NOT EXISTS admin_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_tg_id INTEGER NOT NULL,
    command    TEXT NOT NULL,
    args       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Начальные данные (цены — стартовые, меняются через /price_set)
-- ============================================================
INSERT OR IGNORE INTO services (code, category, name, base_price, unit) VALUES
    ('walk30',         'walk',     'Выгул 30 минут',                    700,  'выгул'),
    ('walk60',         'walk',     'Выгул 60 минут',                    1000,  'выгул'),
    ('sitter_visit',   'sitter',   'Зооняня приходящая',                800,  'час'),
    ('sitter_live',    'sitter',   'Зооняня с проживанием',             3000, 'сутки'),
    ('boarding_home',  'boarding', 'Передержка в частном доме',         1500, 'сутки'),
    ('boarding_hotel', 'boarding', 'Передержка в зоогостинице',         1600, 'сутки'),
    ('taxi',           'taxi',     'Зоотакси',                          189,  'поездка');  -- посадка, дальше по маршруту (taxi.py)

INSERT OR IGNORE INTO price_rules (id, service_id, name, condition_type, effect_type, value, sort_order) VALUES
    (1, NULL, 'Крупная собака',          'pet_size_l',   'percent', 20, 10),
    (2, NULL, 'Срочно (сегодня)',        'urgent_today', 'fixed',   300, 20),
    (3, NULL, 'Выезд за МКАД',           'outside_mkad', 'fixed',   150, 30),
    (4, NULL, 'Ночное время 22:00–07:00','night',        'percent', 30,  40),
    (5, NULL, 'Праздничный день',        'holiday',      'percent', 20,  50);
