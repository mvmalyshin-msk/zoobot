#!/usr/bin/env bash
# Раз в 10 минут: если в GitHub появилась новая версия, подтянуть и перезапустить службу.
set -euo pipefail
APP=/opt/zoobot
cd "$APP"
BRANCH="$(cat .branch 2>/dev/null || echo main)"
git fetch -q origin "$BRANCH"
if [ "$(git rev-parse HEAD)" != "$(git rev-parse "origin/$BRANCH")" ]; then
  git merge --ff-only -q "origin/$BRANCH"
  "$APP/venv/bin/pip" install --quiet -r requirements.txt
  systemctl restart zoobot
  echo "$(date -Is) обновлено до $(git rev-parse --short HEAD)"
fi
