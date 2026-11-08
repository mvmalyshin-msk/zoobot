# Развёртывание на российский сервер (проверка и переезд)

1. Сервер Ubuntu 24.04 (пробный Beget/SmartApe/Selectel, без карты).
2. В веб-консоли сервера от root: `curl -fsSL https://raw.githubusercontent.com/mvmalyshin-msk/zoobot/main/deploy/install.sh | bash`
3. Ввести токен тестового бота (скрыто), свой Telegram ID, прокси для Telegram (или Enter).
4. Проверка: `bash /opt/zoobot/deploy/check.sh`. Сайт открывается по адресу `http://<IP сервера>`.
5. Обновления подтягиваются сами раз в 10 минут, копия базы — ночью (`/opt/zoobot-data/backups`).

Для боевого запуска нужен домен и HTTPS, и отдельный перенос данных; не делать на боевом токене, пока тот же бот работает на Railway (два бота с одним токеном мешают друг другу).
