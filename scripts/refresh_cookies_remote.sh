#!/usr/bin/env bash
# Piped from Windows. Placeholders: __VPS_DIR__ __API_KEY__ __SUDO_B64__
set -e
cd __VPS_DIR__

SYSTEMCTL=/usr/bin/systemctl
restart_ok=0

# 1) passwordless sudo (full path — must match visudo)
if sudo -n "$SYSTEMCTL" restart inpoland-parser 2>/dev/null; then
  restart_ok=1
fi

# 2) optional: sudo password from Windows secret (base64)
if [ "$restart_ok" -ne 1 ] && [ -n "__SUDO_B64__" ]; then
  PASS=$(printf '%s' '__SUDO_B64__' | base64 -d)
  if printf '%s\n' "$PASS" | sudo -S -p '' "$SYSTEMCTL" restart inpoland-parser 2>/dev/null; then
    restart_ok=1
  fi
  unset PASS
fi

if [ "$restart_ok" -ne 1 ]; then
  echo "FAIL: cannot restart inpoland-parser."
  echo "visudo line must be:"
  echo "  u ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart inpoland-parser"
  echo "Test: sudo -n /usr/bin/systemctl restart inpoland-parser && echo OK"
  exit 1
fi

sleep 8
curl -sS --max-time 20 http://127.0.0.1:8001/health || true
echo
if [ -n "__API_KEY__" ]; then
  KEY="__API_KEY__"
else
  KEY=$(grep -E '^API_KEY=' .env | head -1 | cut -d= -f2- | tr -d '\r')
fi
curl -sS --max-time 20 -X POST http://127.0.0.1:8001/api/v1/circuit/reset -H "X-API-Key: $KEY"
echo
