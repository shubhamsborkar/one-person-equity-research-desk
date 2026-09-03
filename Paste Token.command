#!/bin/zsh
# ONLY for the shipped ICICI Direct (Breeze) adapter, whose regulator requires a fresh login
# every trading day. Most other brokers keep an API session alive for weeks or months and never
# need this file. Double-click on a morning you want the home account live: it opens the login
# page, you paste the number after apisession= from the address bar, press Enter, and the desk
# reconnects.
cd "$(dirname "$0")"
LABEL="com.research-desk"
PORT="${DESK_PORT:-8765}"
.venv/bin/python paste_token.py
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "Restarting the desk with the new token..."
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
  for i in {1..20}; do sleep 2; curl -s -m 2 -o /dev/null "http://localhost:$PORT/" && break; done
  echo ""; echo "  The desk is live: http://localhost:$PORT"
else
  echo ""; echo "  Token saved. Start the desk with 'Start Desk.command' (or double-click 'Keep Desk Running.command' once to make it always on)."
fi
echo ""; echo "You can close this window."
