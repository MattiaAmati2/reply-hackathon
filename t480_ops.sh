#!/usr/bin/env bash
set -euo pipefail

SSH_HOST="${T480_HOST:-t480}"
REMOTE_DIR="/home/selfhost/Reply-Hackathon"

usage() {
  echo "Usage: $0 {sync|setup|start|logs|status}"
  exit 1
}

sync_files() {
  echo "[t480_ops] Ensuring remote directory $REMOTE_DIR exists on $SSH_HOST..."
  ssh "$SSH_HOST" "mkdir -p $REMOTE_DIR"
  echo "[t480_ops] Syncing files to $SSH_HOST:$REMOTE_DIR..."
  rsync -avz --delete \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude '*.pyc' \
    --exclude 'data/' \
    --exclude '.DS_Store' \
    ./ "$SSH_HOST:$REMOTE_DIR/"
}

cmd_setup() {
  sync_files
  echo "[t480_ops] Setting up Python .venv and installing requirements on $SSH_HOST..."
  ssh "$SSH_HOST" "cd $REMOTE_DIR && ([ -d .venv ] || python3 -m venv .venv) && .venv/bin/pip install --upgrade pip && .venv/bin/pip install -r requirements.txt"
}

cmd_start() {
  sync_files
  echo "[t480_ops] Restarting Sparring FastAPI server on $SSH_HOST:8101..."
  ssh "$SSH_HOST" "cd $REMOTE_DIR && (fuser -k 8101/tcp || true) && nohup .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8101 > server.log 2>&1 &"
  echo "[t480_ops] Server started in background. Run '$0 logs' to inspect output."
}

cmd_logs() {
  ssh "$SSH_HOST" "tail -f $REMOTE_DIR/server.log"
}

cmd_status() {
  ssh "$SSH_HOST" "ss -tlnp 2>/dev/null | grep ':8101 ' || lsof -i :8101 || echo 'Port 8101 is not listening.'"
}

case "${1:-}" in
  sync)   sync_files ;;
  setup)  cmd_setup ;;
  start)  cmd_start ;;
  logs)   cmd_logs ;;
  status) cmd_status ;;
  *)      usage ;;
esac
