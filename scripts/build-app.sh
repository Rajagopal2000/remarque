#!/bin/bash
# Build an AppLoad app bundle: usage: build-app.sh <app-dir>
# SERVER_URL env var is baked into the app (default: Mac reachable over USB).
set -euo pipefail

APP_DIR="$(cd "$(dirname "$1")/$(basename "$1")" && pwd)"
SERVER_URL="${SERVER_URL:-http://10.11.99.2:8000}"

cd "$APP_DIR"

# Generate Config.qml with the server URL and API token baked in.
API_TOKEN="${API_TOKEN:-}"
sed -e "s|@SERVER_URL@|$SERVER_URL|" -e "s|@API_TOKEN@|$API_TOKEN|" ui/Config.qml.in > ui/Config.qml

# Find Qt's resource compiler: system rcc, or pyside6-rcc (same tool) via uvx.
if command -v rcc >/dev/null 2>&1; then
    RCC="rcc"
else
    RCC="uvx --from pyside6-essentials pyside6-rcc"
fi

rm -rf output
mkdir output
cp manifest.json output/
cp icon.png output/
# format-version 2 + zlib for compatibility with the device's Qt.
$RCC --binary --format-version 2 --compress-algo zlib -o output/resources.rcc application.qrc

echo "Built $(basename "$APP_DIR") with SERVER_URL=$SERVER_URL"
ls -la output/
