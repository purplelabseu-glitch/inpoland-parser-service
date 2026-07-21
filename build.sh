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

# Firefox deps (camoufox) + Chromium — binaries ОБЯЗАТЕЛЬНО под SERVICE_USER / HOME=APP_DIR
# (иначе playwright кладёт в /root/.cache, а systemd User=u ищет в APP_DIR/.cache)
"$PY" -m playwright install-deps firefox chromium || true

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "    Создан .env — скопируйте PROXY_URL/API_KEY с mobilede-parser!"
fi

chown -R "$SERVICE_USER":"$SERVICE_USER" "$APP_DIR"

echo "==> playwright chromium (HOME=$APP_DIR, user=$SERVICE_USER)"
sudo -u "$SERVICE_USER" \
    HOME="$APP_DIR" \
    PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.cache/ms-playwright" \
    "$PY" -m playwright install chromium

echo "==> camoufox fetch"
sudo -u "$SERVICE_USER" HOME="$APP_DIR" "$PY" -m camoufox fetch

# systemd: явный путь к browsers playwright
sed -e "s#/opt/inpoland-parser#${APP_DIR}#g" \
    -e "s#^User=.*#User=${SERVICE_USER}#" \
    -e "s#--port 8001#--port ${PORT}#g" \
    "$UNIT_TEMPLATE" > "$UNIT_TARGET"
# гарантируем PLAYWRIGHT_BROWSERS_PATH в unit
if ! grep -q PLAYWRIGHT_BROWSERS_PATH "$UNIT_TARGET"; then
    sed -i "/^Environment=HOME=/a Environment=PLAYWRIGHT_BROWSERS_PATH=${APP_DIR}/.cache/ms-playwright" "$UNIT_TARGET"
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"

sleep 3
echo
systemctl --no-pager --full status "$SERVICE_NAME" || true
echo
echo "Проверка:  curl http://127.0.0.1:${PORT}/health"
curl -sS "http://127.0.0.1:${PORT}/health" || true
echo
echo "Docs:      http://SERVER_IP:${PORT}/docs"
echo "Логи:      journalctl -u ${SERVICE_NAME} -f"
echo
echo "Не забудьте в .env: PROXY_URL (+ PROXY_REFRESH_URL) как у mobilede, и API_KEY."
