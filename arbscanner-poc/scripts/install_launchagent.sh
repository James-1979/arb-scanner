#!/bin/bash
set -euo pipefail
APP="${1:-/Applications/ArbScanner.app}"
WORKER="$APP/Contents/Resources/ArbScannerWorker"
PLIST="$HOME/Library/LaunchAgents/com.local.arbscanner.worker.plist"
if [ ! -x "$WORKER" ]; then
  echo "Worker not found: $WORKER" >&2
  exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.local.arbscanner.worker</string>
<key>ProgramArguments</key><array><string>$WORKER</string></array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>$HOME/Library/Logs/ArbScannerWorker.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Logs/ArbScannerWorker.err.log</string>
</dict></plist>
PLIST
UID_NOW="$(id -u)"
launchctl bootout "gui/$UID_NOW" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NOW" "$PLIST"
echo "Installed: $PLIST"
