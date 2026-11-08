#!/usr/bin/env bash
# Самопроверка сервера: что работает, а что нет. Результат можно просто сфотографировать и прислать.
APP=/opt/zoobot
set -a; . "$APP/.env"; set +a
ok()   { echo "  ✅ $1"; }
bad()  { echo "  ❌ $1"; }
echo "Служба бота:";            systemctl is-active --quiet zoobot && ok "работает" || bad "не запущена (journalctl -u zoobot -n 30)"
echo "Сайт на этом сервере:";   [ "$(curl -s -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:${PORT:-80}/healthz)" = "200" ] && ok "отвечает" || bad "не отвечает"
echo "Telegram напрямую:";      [ "$(curl -s -m 10 -o /dev/null -w '%{http_code}' https://api.telegram.org)" != "000" ] && ok "доступен" || bad "недоступен (нужен прокси)"
if [ -n "${TELEGRAM_PROXY:-}" ]; then
  echo "Telegram через прокси:"; [ "$(curl -s -m 10 -x "$TELEGRAM_PROXY" -o /dev/null -w '%{http_code}' https://api.telegram.org)" != "000" ] && ok "доступен" || bad "прокси не работает"
fi
echo "Адрес сайта для проверки с телефона: ${WEBAPP_URL}"
echo "Версия: $(git -C "$APP" log -1 --format='%h %s' | cut -c1-70)"
