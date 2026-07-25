#!/bin/bash
# Build and deploy an AppLoad app to the tablet: usage: deploy-app.sh <app-dir>
# Env: DEVICE_HOST (default 10.11.99.1), SERVER_URL (baked into the app), SSH_KEY (optional)
set -euo pipefail

APP_DIR="$(cd "$(dirname "$1")/$(basename "$1")" && pwd)"
DEVICE_HOST="${DEVICE_HOST:-10.11.99.1}"
APP_ID="$(python3 -c "import json; print(json.load(open('$APP_DIR/manifest.json'))['id'])")"
SSH_OPTS=(-o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
if [ -n "${SSH_KEY:-}" ]; then
    SSH_OPTS+=(-i "$SSH_KEY")
fi

"$(dirname "$0")/build-app.sh" "$APP_DIR"

DEST="/home/root/xovi/exthome/appload/$APP_ID"
echo "Deploying to root@$DEVICE_HOST:$DEST"
ssh "${SSH_OPTS[@]}" "root@$DEVICE_HOST" "mkdir -p '$DEST'"
scp "${SSH_OPTS[@]}" "$APP_DIR"/output/* "root@$DEVICE_HOST:$DEST/"
echo "Done. Restart xochitl or reopen the AppLoad menu to pick up changes."
