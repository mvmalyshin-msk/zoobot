#!/usr/bin/env bash
# Установка Зооконсьержа на российский сервер (Ubuntu 24.04). Запускать от root в веб-консоли хостера:
#   curl -fsSL https://raw.githubusercontent.com/mvmalyshin-msk/zoobot/main/deploy/install.sh | bash
# Секреты (токен бота) вводятся здесь, на сервере, скрыто; в чат и в репозиторий они не попадают.
set -euo pipefail

APP=/opt/zoobot
DATA=/opt/zoobot-data
REPO=https://github.com/mvmalyshin-msk/zoobot
BRANCH="${BRANCH:-main}"   # какая ветка GitHub ставится и потом сама обновляется

if [ "$(id -u)" != "0" ]; then echo "Запустите от root."; exit 1; fi

echo "== 1/5 Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y git python3 python3-venv python3-pip sqlite3 curl cron

echo "== 2/5 Код"
if [ -d "$APP/.git" ]; then git -C "$APP" fetch -q origin "$BRANCH" && git -C "$APP" checkout -q "$BRANCH" && git -C "$APP" pull -q --ff-only origin "$BRANCH"
else git clone -q --branch "$BRANCH" "$REPO" "$APP"; fi
echo "$BRANCH" > "$APP/.branch"
python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install --quiet --upgrade pip
"$APP/venv/bin/pip" install --quiet -r "$APP/requirements.txt"
mkdir -p "$DATA/backups"

echo "== 3/5 Настройки"
if [ ! -f "$APP/.env" ]; then
  IP="$(curl -fsS -m 10 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
  echo "Для проверки лучше отдельный тестовый бот (создаётся в @BotFather за минуту), чтобы не мешать боевому."
  read -r -s -p "Токен бота (ввод скрыт): " TOKEN < /dev/tty; echo
  read -r -p "Ваш Telegram ID администратора (число): " ADMIN < /dev/tty
  read -r -p "Прокси для Telegram (если нет, просто Enter): " PROXY < /dev/tty
  umask 077
  {
    echo "BOT_TOKEN=$TOKEN"
    echo "ADMIN_IDS=$ADMIN"
    echo "PORT=80"
    echo "DB_PATH=$DATA/zoobot.db"
    echo "WEBAPP_URL=http://$IP"
    echo "TIMEZONE=Europe/Moscow"
    [ -n "$PROXY" ] && echo "TELEGRAM_PROXY=$PROXY"
  } > "$APP/.env"
  chmod 600 "$APP/.env"
fi

echo "== 4/5 Служба"
cat > /etc/systemd/system/zoobot.service <<UNIT
[Unit]
Description=Zoobot (Зооконсьерж)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$APP
EnvironmentFile=$APP/.env
ExecStart=$APP/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now zoobot
systemctl restart zoobot

echo "== 5/5 Обновления и копии"
cat > /etc/cron.d/zoobot <<CRON
*/10 * * * * root $APP/deploy/update.sh >> /var/log/zoobot-update.log 2>&1
30 3 * * * root $APP/deploy/backup.sh >> /var/log/zoobot-backup.log 2>&1
CRON
chmod 644 /etc/cron.d/zoobot

echo
echo "Готово. Проверка: bash $APP/deploy/check.sh"
