#!/usr/bin/env bash
# Start Caddy + FastAPI + Next.js for the public demo image (single container).
# Caddy (:3000) reverse-proxies /api/* → FastAPI (:8000), else → Next.js (:3001).
set -euo pipefail

cd /app
export PYTHONUNBUFFERED=1

caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
CADDY_PID=$!

uvicorn api.main:app --host 127.0.0.1 --port 8000 &
UV_PID=$!

cd /app/dashboard
npm run start -- -H 127.0.0.1 -p 3001 &
WEB_PID=$!

_term() {
  kill "$CADDY_PID" "$UV_PID" "$WEB_PID" 2>/dev/null || true
}
trap _term SIGTERM SIGINT

# Exit when any child exits (so a crash tears the container down).
wait -n
STATUS=$?
_term
wait || true
exit "$STATUS"
