#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt pyinstaller
rm -rf build dist
pyinstaller --noconfirm --clean --windowed --name ArbScanner --collect-all keyring --add-data "frontend:frontend" app.py
pyinstaller --noconfirm --clean --onefile --name ArbScannerWorker --collect-all keyring worker.py
mkdir -p dist/ArbScanner.app/Contents/Resources
cp dist/ArbScannerWorker dist/ArbScanner.app/Contents/Resources/ArbScannerWorker
chmod +x dist/ArbScanner.app/Contents/Resources/ArbScannerWorker
printf '%s\n' "Built: dist/ArbScanner.app"
printf '%s\n' "The app includes a background worker at Contents/Resources/ArbScannerWorker."
printf '%s\n' "For personal unsigned testing, macOS may require right-click > Open. Distribution requires Apple signing/notarisation."
