# reMarkable AI Reading Assistant

An AI assistant for the reMarkable Paper Pro, integrated into the stock PDF viewer.
You handwrite a question with the pen in a floating panel, optionally scoped to text you highlighted, and read a typeset answer.
A companion service on your Mac does document syncing, highlight extraction, and the LLM calls (Claude by default).

## Architecture

- `server/` - Python FastAPI companion service on the Mac.
  It pulls documents from the tablet via rsync over SSH, detects the currently open document, extracts PDF text (PyMuPDF) and smart-highlight text (rmscene), rasterizes handwriting strokes, and streams answers from Claude (or OpenAI).
- `device-app/` - QML-only AppLoad app that runs inside xochitl on the tablet (via XOVI + rm-appload).
  It captures pen strokes, sends them as JSON to the server, and shows the answer as paginated text.
- `spike/` - M1 throwaway app that verifies the risky assumptions on the device: pen input reaching QML handlers, and HTTP reaching the Mac.
- `scripts/` - build (Qt rcc via pyside6), deploy (scp), and desktop QML validation.

## One-time device setup

1. Sync your notebooks to the cloud, then enable developer mode on the Paper Pro (this factory resets the device).
2. Install the XOVI + AppLoad stack with remagic: `curl -fsSL https://raw.githubusercontent.com/maximerivest/remagic/main/get.sh | sh` (tablet connected over USB).
3. Turn off automatic OS updates in the tablet settings.
4. After any OS update, re-run `remagic setup`.

## Mac setup

```bash
cd server
cp .env.example .env   # set RM_HOST (tablet LAN IP), SSH_KEY_PATH
uv sync
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The default provider is `claude-code`: it runs headless Claude Code under your claude.ai subscription, so no API key is needed as long as Claude Code is installed and logged in.
`PROVIDER=codex` works the same way with the codex CLI and a ChatGPT subscription.
Alternatives: `gemini` (experimental, stateless), `claude` with an `ANTHROPIC_API_KEY`, or `openai` (with `OPENAI_BASE_URL` this also covers gateways like LiteLLM or OpenRouter).

Every ask runs in two stages to save tokens: a cheap model (haiku by default) transcribes the handwriting, and the strong model answers.
For `claude-code` and `codex`, answers run inside a persistent per-document session: the full document text is sent once when the session starts, follow-up questions send only the question plus page and highlight hints, and resumed turns are served mostly from prompt cache (which counts far less against subscription limits).
Sessions expire after `SESSION_TTL_DAYS` (60) idle days; the tablet panel shows the current session and has a "New session" button to clear the context; per-ask token usage is shown in the status line.

Daily-loop polish:

- Undo (last stroke) next to Clear; the scratchpad auto-clears once transcription succeeds, ready for the follow-up.
- Ask becomes Stop while busy: cancelling stops the job server-side and the exchange is not recorded.
- A Retry button appears after failures and re-submits the same strokes or quick action.
- "Brief: on" answers in 3-5 sentences; A-/A+ on the answer view adjust the font (repagination is automatic).
- Document syncs are throttled (`SYNC_MAX_AGE`, default 60s) so rsync stays out of the ask path; `/api/refresh` always syncs.
- The status line shows elapsed seconds while thinking and warns when the answer used stale synced data.
- `/metrics` exposes Prometheus metrics (asks, per-phase latency, tokens by provider, sync failures); the pod carries scrape annotations.

Other panel features:

- Quick actions (one tap, no handwriting): summarize page, summarize document, explain highlights, define the latest highlight.
- "Attach: image" mode renders the current PDF page and sends it to the vision model, for questions about figures and equations.
- History button: paginated view of all past Q&A for the document.
- Export button: generates a "Notes - <title>" PDF of the Q&A history and pushes it onto the tablet (appears after a device restart; verify this path on the device).
- Auth: set `API_TOKEN` on the server and bake the same value into the app (`API_TOKEN=... ./scripts/deploy-app.sh device-app`); requests without the token get 401.
- Anki button: generates a spaced-repetition deck for the whole document (mixed card types: basic for concepts, basic-and-reversed for definitions, cloze for facts and formulas).
  The chat history and highlights are fed into the same generation pass as priority sources: exchanges are distilled into atomic self-contained cards (conversational questions rewritten to stand alone, junk exchanges skipped, overlap with document cards deduplicated).
  Decks are incremental: the server tracks what each deck already covers (chat timestamps, highlight set, document text hash), and pressing Anki again adds cards only for new material; unchanged documents short-circuit without an LLM call.
  The `.apkg` always contains the full merged deck with stable note ids (safe to re-import); AnkiConnect receives only the new notes.
  `POST /api/anki/<doc_id>?mode=full` discards the tracking state and regenerates from scratch.
  The deck is saved as `.apkg` (download at `/api/anki/<doc_id>.apkg`); with `ANKI_CONNECT_URL` set, notes are also pushed via AnkiConnect and synced to AnkiWeb (AnkiWeb has no official API; AnkiConnect is the sanctioned bridge).
  In the cluster this is automatic: the chart runs an Anki container (`chrislongros/anki-desktop`, KasmVNC + AnkiConnect) as a ClusterIP-only sidecar service holding your AnkiWeb login on its own PVC, and the server syncs down, adds notes, and syncs up on every deck.
  One-time setup after the first deploy: port-forward the web UI and log in to AnkiWeb (see `deploy/kubernetes/SECRETS.md`).
- Sessions are retained for 2 years of idle time by default (`SESSION_TTL_DAYS=730`); a matching `cleanupPeriodDays` is passed to Claude Code so it does not prune the session transcripts earlier.

Run the tests with `uv run pytest`.

## Running in Kubernetes (Project-Maya homelab)

The server can run in the cluster instead of a Mac; the cluster and tablet share the LAN, which is the one hard requirement (the pod SSHes to the tablet, the tablet HTTPs to the pod).

This repo never deploys anything: GitHub Actions (`.github/workflows/ci.yml`) only tests and publishes the server image to GHCR (`ghcr.io/rajagopal2000/remarque-server`) on pushes to main.
`deploy/kubernetes/` is a reference chart to vendor into the GitOps repo that owns the cluster.

1. For a private repo, add the GHCR pull secret per `deploy/kubernetes/SECRETS.md`.
2. Copy `deploy/kubernetes/` to `applications/remarque/` in the kubernetes-infrastructure repo (app dir = namespace = release); ArgoCD reconciles from there.
3. Create the two sealed secrets per `deploy/kubernetes/SECRETS.md`: the Claude subscription token (`claude setup-token` on the Mac) and a dedicated tablet SSH key.
4. Pick a free MetalLB IP for `serviceIP` and set `config.rmHost` to the tablet's reserved LAN IP.
5. Deploy the device app with `SERVER_URL=http://<serviceIP>:8000`.

State (synced documents, sqlite sessions and history, Claude Code session transcripts, ssh known_hosts) lives on one Longhorn PVC mounted at `/data`; the deployment uses `Recreate` so there is never a second writer.
Caveats: the subscription token needs resealing about yearly; the codex provider is not wired for in-cluster auth (its auth.json rotates refresh tokens), so use `claude-code` in the cluster.

## Local transcription (no cloud call for handwriting)

Transcription and answering are independent providers, so a local vision model can transcribe while Claude answers.
No code changes needed; any OpenAI-compatible server works (e.g. Ollama in the cluster):

```bash
TRANSCRIBE_PROVIDER=openai
OPENAI_BASE_URL=http://ollama:11434/v1
OPENAI_API_KEY=ollama
OPENAI_TRANSCRIBE_MODEL=qwen2.5vl:7b
```

## Trying the whole thing without the tablet

The simulator runs the actual tablet app QML on the Mac against the local server.

```bash
# Interactive: draw with the mouse, click Ask
uvx --from pyside6-essentials python scripts/simulator.py --server-url http://localhost:8000

# Automated E2E: injects handwriting strokes, clicks Ask, verifies the answer
uvx --from pyside6-essentials python scripts/simulator.py --test --server-url http://localhost:8000
```

## Deploying the apps to the tablet

`SERVER_URL` must be the URL of your Mac as seen from the tablet (give your Mac a DHCP reservation).

```bash
# M1 spike first: verifies pen input + networking inside an AppLoad window
SERVER_URL=http://<mac-ip>:8000 DEVICE_HOST=<tablet-ip> ./scripts/deploy-app.sh spike

# The real assistant
SERVER_URL=http://<mac-ip>:8000 DEVICE_HOST=<tablet-ip> ./scripts/deploy-app.sh device-app
```

On the tablet: open a PDF, open the AppLoad menu, and long-press the app icon to launch it windowed over the document.

## M1 spike verification checklist

1. Launch "AI Pen Spike" windowed over an open PDF.
2. Write with the pen in each of the three strips; note which ones show ink and what the log line reports.
3. Write with a finger in each strip and compare.
4. Tap "Test server" and confirm the log shows `status 200`.
5. If no strip receives pen input, the fallback is a small compiled evdev backend (riddle pattern); the strokes-as-JSON design is unchanged by that swap.

## Validating QML changes without the device

```bash
uvx --from pyside6-essentials python scripts/check-qml.py
```
