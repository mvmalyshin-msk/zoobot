#!/usr/bin/env bash
# Раз в 10 минут: если в GitHub появилась новая версия, подтянуть и перезапустить службу.
set -euo pipefail
APP=/opt/zoobot
cd "$APP"
git fetch -q origin main
if [ "$(git rev-parse HEAD)" != "$(git rev-parse origin/main)" ]; then
  git merge --ff-only -q origin/main
  "$APP/venv/bin/pip" install --quiet -r requirements.txt
  systemctl restart zoobot
  echo "$(date -Is) обновлено до $(git rev-parse --short HEAD)"
fi
