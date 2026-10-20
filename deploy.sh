#!/bin/bash
set -e

APP_DIR="/root/LAPNet/Fraud-monitor"
cd "$APP_DIR"

echo "=== 1. Pulling latest code from Git repository ==="
if [ -d ".git" ]; then
    git pull origin main || git pull origin master || true
fi

echo "=== 2. Starting/Updating Docker Services (PostgreSQL, Redis, Grafana) ==="
docker compose up -d

echo "=== 3. Restarting Fraud Monitor Pipeline Service ==="
systemctl restart fraud-monitor.service

echo "=== 4. Deployment Complete! Checking Service Status ==="
systemctl status fraud-monitor.service --no-pager
