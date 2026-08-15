#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
APP="dist/ArbScanner.app"
DMG="dist/ArbScanner-PoC.dmg"
if [ ! -d "$APP" ]; then
  ./scripts/build_macos.sh
fi
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -R "$APP" "$STAGE/ArbScanner.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -volname "ArbScanner PoC" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
echo "Built: $DMG"
