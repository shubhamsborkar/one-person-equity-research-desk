#!/bin/zsh
# Double-click me ONCE. From then on the desk starts when you log in to this Mac
# and restarts by itself if it ever stops. Double-click "Stop Desk.command" to switch it off.
cd "$(dirname "$0")"
HERE="$(pwd)"
LABEL="com.research-desk"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PORT="${DESK_PORT:-8765}"

if [ ! -x ".venv/bin/python" ]; then
  echo ""
  echo "  The desk is not installed yet (no .venv folder here)."
  echo "  Open this folder in your coding agent and paste the instruction from README.md first."
  echo ""
  read -k1 "?Press any key to close. "
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$HERE/.venv/bin/python</string>
    <string>server.py</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$HERE/logs/desk-service.log</string>
  <key>StandardErrorPath</key><string>$HERE/logs/desk-service.log</string>
</dict>
</plist>
PL

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null
launchctl bootstrap "gui/$(id -u)" "$PLIST"
for i in {1..20}; do sleep 2; curl -s -m 2 -o /dev/null "http://localhost:$PORT/" && break; done
echo ""
if curl -s -m 2 -o /dev/null "http://localhost:$PORT/"; then
  echo "  The desk is running and will start at every login: http://localhost:$PORT"
  echo "  It restarts by itself if it stops. Log: logs/desk-service.log"
else
  echo "  The service is installed but the desk has not answered yet. Give it a minute,"
  echo "  then open http://localhost:$PORT . If it stays blank, read logs/desk-service.log"
  echo "  or paste that log to your coding agent."
fi
echo ""
echo "You can close this window."
