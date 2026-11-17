#!/usr/bin/env bash
# Run NEWSWIRE locally without Docker: API (8000), worker and web UI (3000).
# Prerequisites: PostgreSQL running, `createdb newswire`, backend/.venv with requirements-dev.txt,
# and `npm ci` in frontend/. Stop with Ctrl+C.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export NEWSWIRE_DATABASE_URL="${NEWSWIRE_DATABASE_URL:-postgresql+psycopg://localhost/newswire}"

cd "$ROOT/backend"
.venv/bin/python -m app.cli migrate
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload &
API=$!
.venv/bin/python -m app.worker &
WORKER=$!
cd "$ROOT/frontend"
npm run dev &
WEB=$!

trap 'kill $API $WORKER $WEB 2>/dev/null' EXIT INT TERM
echo "NEWSWIRE: http://localhost:3000  (API docs: http://localhost:3000/api/docs)"
wait
