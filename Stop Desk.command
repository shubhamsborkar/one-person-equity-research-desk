#!/bin/zsh
# Double-click me to switch the always-on desk OFF. Double-click again to switch it back ON.
LABEL="com.research-desk"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
echo ""
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL"
  echo "  The desk is OFF and will not start at login. Double-click this file again to turn it back on."
elif [ -f "$PLIST" ]; then
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  echo "  The desk is ON again and will start at every login."
else
  echo "  The always-on service is not installed. Double-click 'Keep Desk Running.command' first."
fi
echo ""
echo "You can close this window."
