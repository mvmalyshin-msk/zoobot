# Зооконсьерж — Telegram-бот

aiogram 3 + SQLite (aiosqlite) + APScheduler. Запуск: `python bot.py`.
Переменные (см. `.env.example`): BOT_TOKEN, ADMIN_IDS, DB_PATH, POLICY_URL, TIMEZONE.
Админ-команды: `/admin` в боте. Проверка без Telegram: `python tests/e2e_test.py`.
На Railway база должна лежать на томе: `DB_PATH=/data/zoobot.db`.

## Запуск на Railway
Переменные сервиса (Variables): `BOT_TOKEN` (от @BotFather) и `ADMIN_IDS` (Telegram-ID админа цифрами) обязательны;
`DB_PATH=/data/zoobot.db` (том `/data`), `POLICY_URL` (ссылка на политику обработки ПДн), `TIMEZONE=Europe/Moscow`.
Токены в чат и в репозиторий не отправляем — только в Variables.
