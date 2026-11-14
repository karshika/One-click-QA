#!/usr/bin/env bash
# Starts the demo app (:8001), backend (:8000) and frontend (:3000) for local development (macOS/Linux).
# First time: see README "Local setup".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || { echo "Run: python3 -m venv .venv && .venv/bin/pip install -e 'backend[dev]' -e demo-app"; exit 1; }
export ALLOW_LOCAL_TARGETS="${ALLOW_LOCAL_TARGETS:-true}"
trap 'kill 0' EXIT
(cd "$ROOT/demo-app" && "$PY" -m uvicorn demo_app.main:app --port 8001) &
(cd "$ROOT/backend" && "$PY" -m uvicorn app.main:app --port 8000) &
(cd "$ROOT/frontend" && npm run dev) &
echo "Demo app  http://localhost:8001"
echo "Backend   http://localhost:8000/docs"
echo "Dashboard http://localhost:3000   (Ctrl+C to stop)"
wait
