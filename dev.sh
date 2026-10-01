#!/usr/bin/env bash
# Run the Tender Evaluator locally: backend on :8010, frontend on :5180.
#   ./dev.sh            start both (Ctrl-C stops both)
# The backend is restarted if it exits with an error; jobs a stop interrupted resume on start.
set -u
set -m  # each server gets its own process group, so Ctrl-C reaches only this script, which stops them once
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

(cd "$ROOT/frontend" && { [ -d node_modules ] || npm install; }) || exit 1
(cd "$ROOT/backend" && uv sync -q) || exit 1

run_backend() {
  cd "$ROOT/backend" || exit 1
  local fast_failures=0 started code
  while :; do
    started=$(date +%s)
    ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT"
    code=$?
    case $code in 0|130|143) exit 0 ;; esac
    if [ $(( $(date +%s) - started )) -lt 15 ]; then fast_failures=$((fast_failures + 1)); else fast_failures=1; fi
    if [ "$fast_failures" -ge 5 ]; then
      echo "Backend failed to start 5 times in a row (exit $code) — giving up; see the error above." >&2
      exit "$code"
    fi
    echo "Backend exited with code $code — restarting in 2 s…" >&2
    sleep 2
  done
}

BACKEND_PG="" FRONTEND_PG="" STOPPED=0
cleanup() {
  [ "$STOPPED" -eq 1 ] && return
  STOPPED=1
  set +m  # no "Terminated: 15" job notices for servers we are stopping on purpose
  echo; echo "Stopping…"
  for pg in $BACKEND_PG $FRONTEND_PG; do kill -TERM -- "-$pg" 2>/dev/null; done
  for _ in $(seq 1 50); do  # up to 10 s to shut down gracefully
    alive=0
    for pg in $BACKEND_PG $FRONTEND_PG; do kill -0 -- "-$pg" 2>/dev/null && alive=1; done
    [ "$alive" -eq 0 ] && break
    sleep 0.2
  done
  for pg in $BACKEND_PG $FRONTEND_PG; do kill -KILL -- "-$pg" 2>/dev/null; done
  echo "Stopped."
}
trap 'cleanup; exit 0' INT TERM
trap cleanup EXIT

run_backend </dev/null &
BACKEND_PG=$!
(cd "$ROOT/frontend" && TE_API_URL="http://127.0.0.1:$BACKEND_PORT" exec ./node_modules/.bin/vite --port "$FRONTEND_PORT" --strictPort) </dev/null &
FRONTEND_PG=$!

echo "Backend  → http://localhost:$BACKEND_PORT"
echo "Frontend → http://localhost:$FRONTEND_PORT"
wait "$BACKEND_PG"  # the backend giving up ends the session; Ctrl-C stops both
