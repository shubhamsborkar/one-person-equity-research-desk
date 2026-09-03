#!/bin/zsh
# Double-click me to start the desk.
cd "$(dirname "$0")"

if curl -s -m 2 http://localhost:${DESK_PORT:-8765} >/dev/null 2>&1; then
  echo ""
  echo "  The desk is ALREADY running."
  echo "  Open the 'Live Desk' note in Obsidian, or http://localhost:${DESK_PORT:-8765}"
  echo ""
  exit 0
fi

source .venv/bin/activate
python server.py
