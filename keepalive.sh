#!/bin/bash
# Keep-alive для Render.com (бесплатный тариф засыпает через 15 мин)
# Запускается отдельно как separate service
while true; do
  curl -sf https://zoobot.onrender.com/healthz > /dev/null 2>&1
  sleep 8m
done
