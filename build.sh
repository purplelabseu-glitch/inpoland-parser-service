#!/usr/bin/env bash
#
# build.sh — разворачивание in-poland parser на Debian 12 (рядом с mobilede).
# Порт по умолчанию: 8001
#
#   sudo bash build.sh
#   (не sh/dash — нужен bash из-за pipefail)
#
# Если ошибка «set: pipefail» / «недопустимое название параметра»:
#   в файле CRLF с Windows. На сервере:
#   sed -i 's/\r$//' build.sh && sudo bash build.sh
#
if [ -z "${BASH_VERSION:-}" ]; then
    echo "ОШИБКА: запускайте через bash: sudo bash $0" >&2
    exit 1
fi
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "ОШИБКА: запустите от root (sudo bash build.sh)" >&2
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-www-data}}"
SERVICE_NAME="inpoland-parser"
UNIT_TEMPLATE="$APP_DIR/deploy/inpoland-parser.service"
UNIT_TARGET="/etc/systemd/system/${SERVICE_NAME}.service"
VENV="$APP_DIR/.venv"
PY="$VENV/bin/python"
PORT="${PORT:-8001}"

echo "==> Каталог: $APP_DIR"
echo "==> User: $SERVICE_USER  Port: $PORT"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip curl ca-certificates

if [ ! -d "$VENV" ]; then
    python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
"$PY" -m pip install --upgrade pip
"$PY" -m pip install -r "$APP_DIR/requirements.txt"

# Firefox deps (camoufox) + Chromium browser binary
"$PY" -m playwright install-deps firefox chromium || true
"$PY" -m playwright install chromium

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "    Создан .env — скопируйте PROXY_URL/API_KEY с mobilede-parser!"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "==> camoufox fetch"
sudo -u "$SERVICE_USER" HOME="$APP_DIR" "$PY" -m camoufox fetch

sed -e "s#/opt/inpoland-parser#${APP_DIR}#g" \
    -e "s#^User=.*#User=${SERVICE_USER}#" \
    -e "s#--port 8001#--port ${PORT}#g" \
    "$UNIT_TEMPLATE" > "$UNIT_TARGET"

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

echo
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "Проверка:  curl http://127.0.0.1:${PORT}/health"
echo "Docs:      http://SERVER_IP:${PORT}/docs"
echo "Логи:      journalctl -u ${SERVICE_NAME} -f"
echo
echo "Не забудьте в .env: PROXY_URL (+ PROXY_REFRESH_URL) как у mobilede, и API_KEY."
