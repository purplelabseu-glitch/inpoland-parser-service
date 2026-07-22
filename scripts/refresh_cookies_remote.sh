#!/usr/bin/env bash
# Placeholders replaced by refresh_cookies.ps1: __VPS_DIR__ __API_KEY__ __SUDO_B64__
set -e
cd __VPS_DIR__

restart_ok=0
SUDO_B64='__SUDO_B64__'
API_KEY_INLINE='__API_KEY__'

echo "try passwordless /bin/systemctl ..."
if sudo -n /bin/systemctl restart inpoland-parser; then
  echo "restart OK: sudo -n /bin/systemctl"
  restart_ok=1
else
  echo "passwordless failed (exit $?)"
fi

if [ "$restart_ok" -ne 1 ]; then
  echo "try passwordless /usr/bin/systemctl ..."
  if sudo -n /usr/bin/systemctl restart inpoland-parser; then
    echo "restart OK: sudo -n /usr/bin/systemctl"
    restart_ok=1
  else
    echo "passwordless failed (exit $?)"
  fi
fi

if [ "$restart_ok" -ne 1 ]; then
  if [ -z "$SUDO_B64" ] || [ "$SUDO_B64" = "__SUDO_B64__" ]; then
    echo "FAIL: no sudo password provided from Windows secret file"
    exit 1
  fi
  echo "try sudo -S with password from secret..."
  PASS=$(printf '%s' "$SUDO_B64" | base64 -d)
  set +e
  printf '%s\n' "$PASS" | sudo -S -p '' /bin/systemctl restart inpoland-parser
  rc=$?
  set -e
  unset PASS
  if [ "$rc" -eq 0 ]; then
    echo "restart OK: sudo -S /bin/systemctl"
    restart_ok=1
  else
    echo "FAIL: sudo -S failed rc=$rc"
    exit 1
  fi
fi

sleep 8
curl -sS --max-time 20 http://127.0.0.1:8001/health || true
echo
if [ -n "$API_KEY_INLINE" ] && [ "$API_KEY_INLINE" != "__API_KEY__" ]; then
  KEY="$API_KEY_INLINE"
else
  KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
fi
curl -sS --max-time 20 -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: $KEY"
echo
