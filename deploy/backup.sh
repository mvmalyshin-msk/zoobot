#!/usr/bin/env bash
# Ночная копия базы (целостная, через sqlite3 .backup); хранятся последние 14.
set -euo pipefail
DATA=/opt/zoobot-data
[ -f "$DATA/zoobot.db" ] || exit 0
sqlite3 "$DATA/zoobot.db" ".backup '$DATA/backups/zoobot-$(date +%F).db'"
ls -1t "$DATA"/backups/zoobot-*.db | tail -n +15 | xargs -r rm -f
