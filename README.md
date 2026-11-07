# Зооконсьерж — Telegram-бот

aiogram 3 + SQLite (aiosqlite) + APScheduler. Запуск: `python bot.py`.
Переменные (см. `.env.example`): BOT_TOKEN, ADMIN_IDS, DB_PATH, POLICY_URL, TIMEZONE.
Админ-команды: `/admin` в боте. Проверка без Telegram: `python tests/e2e_test.py`.
На Railway база должна лежать на томе: `DB_PATH=/data/zoobot.db`.
