#!/usr/bin/env bash
# Restaurant GM — start/stop the whole stack (backend + 5 plumbing listeners).
#
#   ./start.sh           start everything (logs in ./logs/, PIDs in ./logs/pids)
#   ./start.sh stop      stop everything (incl. stray MCP server processes)
#   ./start.sh status    show what's running
#   ./start.sh reset     stop everything + factory-reset MongoDB to seed state
#
# The simulator is NOT started here — drive days from the dashboard's ▶ button
# (http://localhost:8000), or run `python -m plumbing.simulator` manually.

set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-$HOME/venvs/restaurant_gm/bin/python}"
UVICORN="${UVICORN:-$HOME/venvs/restaurant_gm/bin/uvicorn}"
LOGS="./logs"
PIDFILE="$LOGS/pids"

SERVICES=(backend depletion rollups replenishment reconcile worker)

cmd_for() {
  case "$1" in
    backend)       echo "$UVICORN backend.app:app --host 0.0.0.0 --port 8000" ;;
    depletion)     echo "$PY -u -m plumbing.depletion" ;;
    rollups)       echo "$PY -u -m plumbing.rollups" ;;
    replenishment) echo "$PY -u -m plumbing.replenishment" ;;
    reconcile)     echo "$PY -u -m plumbing.reconcile" ;;
    worker)        echo "$PY -u -m plumbing.worker" ;;
  esac
}

start() {
  [ -f .env ] || { echo "ERROR: no .env file (need MONGODB_CONNECTION_STRING, GOOGLE_API_KEY)"; exit 1; }
  [ -x "$PY" ] || { echo "ERROR: python not found at $PY (set PYTHON=...)"; exit 1; }
  mkdir -p "$LOGS"
  if [ -f "$PIDFILE" ] && status_quiet; then
    echo "Already running (./start.sh status). Run ./start.sh stop first."
    exit 1
  fi
  # Kill strays from manual terminal runs — twin listeners (especially a second
  # worker) cause duplicate agent actions; cooldowns are per-process memory.
  if pgrep -f "plumbing\.(depletion|rollups|replenishment|reconcile|worker|simulator)|uvicorn backend.app" >/dev/null 2>&1; then
    echo "  killing stray processes from manual runs…"
    pkill -f "plumbing\.(depletion|rollups|replenishment|reconcile|worker|simulator)" 2>/dev/null || true
    pkill -f "uvicorn backend.app" 2>/dev/null || true
    sleep 2
    pkill -9 -f "mongodb-mcp-server" 2>/dev/null || true
  fi
  : > "$PIDFILE"
  for svc in "${SERVICES[@]}"; do
    $(cmd_for "$svc") >> "$LOGS/$svc.log" 2>&1 &
    echo "$!:$svc" >> "$PIDFILE"
    echo "  started $svc (pid $!) → logs/$svc.log"
  done
  echo
  echo "Waiting for the worker to load the agents…"
  for _ in $(seq 1 60); do
    grep -q "Worker watching" "$LOGS/worker.log" 2>/dev/null && break
    sleep 1
  done
  grep -q "Worker watching" "$LOGS/worker.log" 2>/dev/null \
    && echo "Worker ready." \
    || echo "WARNING: worker not ready yet — check logs/worker.log"
  echo
  echo "Dashboard: http://localhost:8000  (▶ Run a day lives in the topbar)"
  echo "Tail logs: tail -f logs/*.log     Stop: ./start.sh stop"
}

stop() {
  if [ -f "$PIDFILE" ]; then
    while IFS=: read -r pid svc; do
      kill "$pid" 2>/dev/null && echo "  stopped $svc ($pid)" || true
    done < "$PIDFILE"
    rm -f "$PIDFILE"
  fi
  # belt and suspenders: catch strays from manual runs + leaked MCP servers
  pkill -f "plumbing.simulator" 2>/dev/null || true
  pkill -f "plumbing\.(depletion|rollups|replenishment|reconcile|worker)" 2>/dev/null || true
  pkill -f "uvicorn backend.app" 2>/dev/null || true
  sleep 1
  pkill -9 -f "mongodb-mcp-server" 2>/dev/null || true
  echo "All stopped."
}

status_quiet() {
  local any=1
  [ -f "$PIDFILE" ] || return 1
  while IFS=: read -r pid svc; do
    kill -0 "$pid" 2>/dev/null && any=0
  done < "$PIDFILE"
  return $any
}

status() {
  [ -f "$PIDFILE" ] || { echo "Not running (no $PIDFILE)."; exit 0; }
  while IFS=: read -r pid svc; do
    if kill -0 "$pid" 2>/dev/null; then
      echo "  $svc (pid $pid): running"
    else
      echo "  $svc (pid $pid): DEAD — check logs/$svc.log"
    fi
  done < "$PIDFILE"
}

reset() {
  stop
  echo
  "$PY" -m plumbing.factory_reset
  echo
  echo "Run ./start.sh to bring the stack back up."
}

case "${1:-start}" in
  start)  start ;;
  stop)   stop ;;
  status) status ;;
  reset)  reset ;;
  *) echo "usage: ./start.sh [start|stop|status|reset]"; exit 1 ;;
esac
