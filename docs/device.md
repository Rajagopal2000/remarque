# Device setup and development workflow

## One-time device setup

1. Sync your notebooks to the cloud, then enable developer mode on the Paper Pro (this factory resets the device).
2. Install the XOVI + AppLoad stack with remagic: `curl -fsSL https://raw.githubusercontent.com/maximerivest/remagic/main/get.sh | sh` (tablet connected over USB).
3. Turn off automatic OS updates in the tablet settings.
4. After any OS update, re-run `remagic setup`.

## Deploying the apps to the tablet

`SERVER_URL` is the server as seen from the tablet: the Mac's LAN IP during development, or the MetalLB `serviceIP` in the cluster.
`API_TOKEN` must match the server's token (omit both only in tokenless local development).

```bash
# Spike first: verifies pen input + networking inside an AppLoad window
SERVER_URL=http://<server-ip>:8000 DEVICE_HOST=<tablet-ip> ./scripts/deploy-app.sh spike

# The real assistant
SERVER_URL=http://<server-ip>:8000 API_TOKEN=<token> DEVICE_HOST=<tablet-ip> ./scripts/deploy-app.sh device-app
```

On the tablet: open a PDF, open the AppLoad menu, and long-press the app icon to launch it windowed over the document.

## Spike verification checklist (first time on the device)

1. Launch "AI Pen Spike" windowed over an open PDF.
2. Write with the pen in each of the three strips; note which ones show ink and what the log line reports.
3. Write with a finger in each strip and compare.
4. Tap "Test server" and confirm the log shows `status 200`.
5. If no strip receives pen input, the fallback is a small compiled evdev backend (riddle pattern); the strokes-as-JSON design is unchanged by that swap.

## Trying the whole thing without the tablet

The simulator runs the actual tablet app QML on the Mac against the local server.

```bash
# Interactive: draw with the mouse, click Ask
uvx --from pyside6-essentials python scripts/simulator.py --server-url http://localhost:8000

# Automated E2E: injects handwriting strokes, clicks Ask, verifies the answer
uvx --from pyside6-essentials python scripts/simulator.py --test --server-url http://localhost:8000
```

## Validating QML changes without the device

```bash
uvx --from pyside6-essentials python scripts/check-qml.py
```
