#!/usr/bin/env bash
set -euo pipefail

SSH_HOST="${T480_HOST:-t480}"
REMOTE_DIR="${T480_REMOTE_DIR:-/home/selfhost/Reply-Hackathon}"
REPO_URL="${REPO_URL:-https://github.com/MattiaAmati2/reply-hackathon.git}"
BRANCH="${BRANCH:-main}"
PORT="${PORT:-8101}"
HOST="${HOST:-0.0.0.0}"

usage() {
  echo "Usage: $0 {pull|sync|setup|start|stop|logs|status}"
  exit 1
}

sync_repo() {
  echo "[t480_ops] Syncing $BRANCH from $REPO_URL on $SSH_HOST:$REMOTE_DIR..."
  ssh "$SSH_HOST" bash -s -- "$REMOTE_DIR" "$REPO_URL" "$BRANCH" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
REPO_URL="$2"
BRANCH="$3"

if [ -d "$REMOTE_DIR/.git" ]; then
  cd "$REMOTE_DIR"
  git fetch origin "$BRANCH"
  git checkout "$BRANCH"
  git pull --ff-only origin "$BRANCH"
elif [ -e "$REMOTE_DIR" ]; then
  echo "[t480_ops] ERROR: $REMOTE_DIR exists without .git. Move or remove it before cloning." >&2
  exit 1
else
  mkdir -p "$(dirname "$REMOTE_DIR")"
  git clone --branch "$BRANCH" "$REPO_URL" "$REMOTE_DIR"
fi
EOF
}

cmd_setup() {
  sync_repo
  echo "[t480_ops] Setting up Python .venv and installing requirements on $SSH_HOST..."
  ssh "$SSH_HOST" bash -s -- "$REMOTE_DIR" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
cd "$REMOTE_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
EOF
}

cmd_start() {
  sync_repo
  echo "[t480_ops] Starting Sparring FastAPI server on $SSH_HOST:$PORT..."
  ssh "$SSH_HOST" bash -s -- "$REMOTE_DIR" "$HOST" "$PORT" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
HOST="$2"
PORT="$3"
cd "$REMOTE_DIR"

if [ ! -x .venv/bin/uvicorn ]; then
  echo "[t480_ops] .venv missing or incomplete; installing requirements..."
  [ -d .venv ] || python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

fuser -k "${PORT}/tcp" >/dev/null 2>&1 || true
sleep 1

nohup .venv/bin/uvicorn backend.main:app --host "$HOST" --port "$PORT" > server.log 2>&1 < /dev/null &
echo $! > server.pid

for _ in $(seq 1 15); do
  if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
if curl -sf "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "[t480_ops] Server is online."
  if [ -n "$TS_IP" ]; then
    echo "[t480_ops] Tailnet URL: http://${TS_IP}:${PORT}"
  fi
else
  echo "[t480_ops] WARNING: Health check failed; recent server.log:" >&2
  tail -n 30 server.log >&2 || true
  exit 1
fi
EOF
}

cmd_stop() {
  echo "[t480_ops] Stopping Sparring server on $SSH_HOST:$PORT..."
  ssh "$SSH_HOST" "fuser -k ${PORT}/tcp >/dev/null 2>&1 || true; rm -f ${REMOTE_DIR}/server.pid"
}

cmd_logs() {
  ssh "$SSH_HOST" "tail -f $REMOTE_DIR/server.log"
}

cmd_status() {
  ssh "$SSH_HOST" bash -s -- "$REMOTE_DIR" "$PORT" <<'EOF'
set -euo pipefail
REMOTE_DIR="$1"
PORT="$2"
ss -tlnp 2>/dev/null | grep ":${PORT} " || lsof -i ":${PORT}" || echo "Port $PORT is not listening."
TS_IP="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
if [ -n "$TS_IP" ]; then
  echo "Tailnet URL: http://${TS_IP}:${PORT}"
fi
if curl -sf "http://127.0.0.1:${PORT}/api/health"; then
  echo ""
fi
EOF
}

case "${1:-}" in
  pull|sync) sync_repo ;;
  setup)     cmd_setup ;;
  start)     cmd_start ;;
  stop)      cmd_stop ;;
  logs)      cmd_logs ;;
  status)    cmd_status ;;
  *)         usage ;;
esac
