#!/usr/bin/env bash
# Run the Tender Evaluator locally: backend on :8010, frontend on :5180.
#   ./dev.sh            start both (Ctrl-C stops both)
# The backend is restarted automatically if it ever exits unexpectedly.
set -u
cd "$(dirname "$0")"
ROOT=$(pwd)
BACKEND_PORT=${BACKEND_PORT:-8010}
FRONTEND_PORT=${FRONTEND_PORT:-5180}

for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
  if lsof -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Port $port is already in use — stop whatever is running there first (lsof -iTCP:$port -sTCP:LISTEN)." >&2
    exit 1
  fi
done

(cd "$ROOT/frontend" && [ -d node_modules ] || npm install)
(cd "$ROOT/backend" && uv sync -q)

stopping=0
cleanup() {
  stopping=1
  kill "$BACKEND_LOOP" "$FRONTEND" 2>/dev/null
  pkill -P "$BACKEND_LOOP" 2>/dev/null
  wait 2>/dev/null
  echo "Stopped."
}
trap cleanup INT TERM EXIT

(
  while [ "$stopping" -eq 0 ]; do
    (cd "$ROOT/backend" && uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT")
    code=$?
    [ "$code" -eq 0 ] || [ "$code" -eq 130 ] || [ "$code" -eq 143 ] && break
    echo "Backend exited with code $code — restarting in 2 s…" >&2
    sleep 2
  done
) &
BACKEND_LOOP=$!

(cd "$ROOT/frontend" && TE_API_URL="http://127.0.0.1:$BACKEND_PORT" npm run dev -- --port "$FRONTEND_PORT" --strictPort) &
FRONTEND=$!

echo "Backend  → http://localhost:$BACKEND_PORT"
echo "Frontend → http://localhost:$FRONTEND_PORT"
wait
