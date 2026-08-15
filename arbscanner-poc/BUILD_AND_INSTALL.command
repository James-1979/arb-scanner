#!/bin/bash
set -eu

ROOT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
cd "$ROOT_DIR"

EXPECTED_VERSION="1.0"
LABEL="com.local.arbscanner.worker"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NOW="$(id -u)"
WAS_INSTALLED=0

printf '%s\n' "ArbScanner installer location: $ROOT_DIR"

if [ ! -f "arbscanner/__init__.py" ] || [ ! -f "frontend/index.html" ]; then
  printf '%s\n' "ERROR: This is not a complete ArbScanner source folder."
  printf '%s\n' "Expected arbscanner/__init__.py and frontend/index.html beside this installer."
  exit 1
fi

SOURCE_VERSION="$(awk -F'"' '/^__version__[[:space:]]*=/ {print $2; exit}' arbscanner/__init__.py)"
if [ -z "$SOURCE_VERSION" ]; then
  printf '%s\n' "ERROR: Could not read ArbScanner source version from arbscanner/__init__.py."
  exit 1
fi

printf '%s\n' "Detected source version: $SOURCE_VERSION"

if [ "$SOURCE_VERSION" != "$EXPECTED_VERSION" ]; then
  printf '%s\n' "ERROR: This installer expects ArbScanner $EXPECTED_VERSION but this folder contains $SOURCE_VERSION."
  printf '%s\n' "Do not build from an older arbscanner-poc folder. Extract the ArbScanner v1.0 release package and run its installer there."
  exit 1
fi

if [ ! -f "RELEASE_NOTES.md" ]; then
  printf '%s\n' "ERROR: RELEASE_NOTES.md is missing. Refusing to build an incomplete package."
  exit 1
fi
if ! grep -Eq "^#{1,6}[[:space:]]+$EXPECTED_VERSION([[:space:]]|$)" RELEASE_NOTES.md; then
  printf '%s\n' "ERROR: RELEASE_NOTES.md does not contain a Markdown release heading for $EXPECTED_VERSION."
  exit 1
fi

if ! grep -Fq "<title>ArbScanner v$EXPECTED_VERSION</title>" frontend/index.html; then
  printf '%s\n' "ERROR: frontend/index.html does not contain the v$EXPECTED_VERSION title marker."
  exit 1
fi

if ! grep -Fq "ArbScanner v$EXPECTED_VERSION" frontend/index.html; then
  printf '%s\n' "ERROR: frontend/index.html does not contain the visible $EXPECTED_VERSION UI marker."
  exit 1
fi

printf '%s\n' "Preflight OK: source and frontend are ArbScanner v$EXPECTED_VERSION."

if [ "${1:-}" = "--verify-only" ]; then
  printf '%s\n' "VERIFY ONLY complete. No build, install, worker or application process was changed."
  exit 0
fi

if [ -f "$PLIST" ]; then
  WAS_INSTALLED=1
  printf '%s\n' "Stopping existing ArbScanner background worker..."
  launchctl bootout "gui/$UID_NOW" "$PLIST" 2>/dev/null || true
fi

if pgrep -x "ArbScanner" >/dev/null 2>&1; then
  printf '%s\n' "Stopping running ArbScanner app before replacement..."
  osascript -e 'tell application "ArbScanner" to quit' 2>/dev/null || true
  WAIT_COUNT=0
  while pgrep -x "ArbScanner" >/dev/null 2>&1 && [ "$WAIT_COUNT" -lt 20 ]; do
    sleep 0.25
    WAIT_COUNT=$((WAIT_COUNT + 1))
  done
  if pgrep -x "ArbScanner" >/dev/null 2>&1; then
    printf '%s\n' "ArbScanner did not exit cleanly; terminating the old process..."
    pkill -TERM -x "ArbScanner" 2>/dev/null || true
    sleep 1
  fi
fi

printf '%s\n' "Building ArbScanner v$EXPECTED_VERSION from: $ROOT_DIR"
./scripts/build_macos.sh

if [ ! -d "dist/ArbScanner.app" ]; then
  printf '%s\n' "ERROR: Build completed without dist/ArbScanner.app."
  exit 1
fi

BUILT_INDEX="$(find "dist/ArbScanner.app" -type f -path '*/frontend/index.html' -print -quit)"
if [ -z "$BUILT_INDEX" ]; then
  printf '%s\n' "ERROR: Built app does not contain frontend/index.html."
  exit 1
fi
if ! grep -Fq "ArbScanner v$EXPECTED_VERSION" "$BUILT_INDEX"; then
  printf '%s\n' "ERROR: Built app frontend is not ArbScanner v$EXPECTED_VERSION."
  exit 1
fi
printf '%s\n' "Built app verification OK: $EXPECTED_VERSION frontend embedded."

printf '%s\n' "Installing /Applications/ArbScanner.app..."
rm -rf "/Applications/ArbScanner.app"
ditto "dist/ArbScanner.app" "/Applications/ArbScanner.app"

INSTALLED_INDEX="$(find "/Applications/ArbScanner.app" -type f -path '*/frontend/index.html' -print -quit)"
if [ -z "$INSTALLED_INDEX" ]; then
  printf '%s\n' "ERROR: Installed app does not contain frontend/index.html."
  exit 1
fi
if ! grep -Fq "ArbScanner v$EXPECTED_VERSION" "$INSTALLED_INDEX"; then
  printf '%s\n' "ERROR: Installed application failed the $EXPECTED_VERSION frontend verification."
  exit 1
fi

if [ "$WAS_INSTALLED" -eq 1 ]; then
  printf '%s\n' "Reinstalling background worker..."
  ./scripts/install_launchagent.sh "/Applications/ArbScanner.app"
fi

printf '\n%s\n' "Installed ArbScanner v$EXPECTED_VERSION."
printf '%s\n' "Verified source, built app and installed frontend are all $EXPECTED_VERSION."
printf '%s\n' "Worker logs: $HOME/Library/Logs/ArbScannerWorker.log"
printf '%s\n' "Worker errors: $HOME/Library/Logs/ArbScannerWorker.err.log"
printf '%s\n\n' "Launching a fresh ArbScanner process..."
open -n "/Applications/ArbScanner.app"
