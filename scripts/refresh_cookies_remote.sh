#!/usr/bin/env bash
set -e
cd __VPS_DIR__

restart_ok=0

try_restart() {
  local cmd="$1"
  local out
  if out=$(sudo -n $cmd 2>&1); then
    echo "restart OK: sudo -n $cmd"
    return 0
  fi
  echo "sudo -n failed ($cmd): $out"
  return 1
}

if try_restart "/bin/systemctl restart inpoland-parser"; then
  restart_ok=1
elif try_restart "/usr/bin/systemctl restart inpoland-parser"; then
  restart_ok=1
fi

SUDO_B64='__SUDO_B64__'
if [ "$restart_ok" -ne 1 ] && [ -n "$SUDO_B64" ] && [ "$SUDO_B64" != "__SUDO_B64__" ]; then
  PASS=$(printf '%s' "$SUDO_B64" | base64 -d)
  for cmd in "/bin/systemctl restart inpoland-parser" "/usr/bin/systemctl restart inpoland-parser"; do
    if printf '%s\n' "$PASS" | sudo -S -p '' $cmd 2>/tmp/inpoland_sudo_err; then
      echo "restart OK: sudo -S $cmd"
      restart_ok=1
      break
    fi
    echo "sudo -S failed ($cmd): $(cat /tmp/inpoland_sudo_err 2>/dev/null || true)"
  done
  unset PASS
fi

if [ "$restart_ok" -ne 1 ]; then
  echo "FAIL: cannot restart inpoland-parser."
  exit 1
fi

sleep 8
curl -sS --max-time 20 http://127.0.0.1:8001/health || true
echo
API_KEY_INLINE='__API_KEY__'
if [ -n "$API_KEY_INLINE" ] && [ "$API_KEY_INLINE" != "__API_KEY__" ]; then
  KEY="$API_KEY_INLINE"
else
  KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
fi
curl -sS --max-time 20 -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: $KEY"
echo
