---
name: verify
description: E2E-verify Remarque changes on a Mac without the tablet - local server with isolated data dir plus the QML simulator.
---

# Verifying Remarque without the tablet

## Handle

1. Seed a document into an isolated sync dir (mimics a prior tablet sync; no real tablet needed).
   Use `server/.venv/bin/python` with pymupdf to write `<doc_id>.pdf`, `<doc_id>.metadata`
   (`{"type": "DocumentType", "visibleName": ..., "lastOpened": "<ms>", "lastOpenedPage": 0}`),
   and `<doc_id>.content` (`{"pageCount": 1, "fileType": "pdf"}`) into `$DATA/xochitl`.
2. Start the server with the isolated data dir (config reads `server/.env`; exported env vars win):

   ```bash
   cd server && REMARQUE_DATA_DIR=$DATA .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8123
   ```

3. Drive the real QML app (full E2E, real LLM calls via the claude-code provider):

   ```bash
   TOKEN=$(grep '^API_TOKEN=' server/.env | cut -d= -f2-)
   uvx --from pyside6-essentials python scripts/simulator.py --test \
       --server-url http://127.0.0.1:8123 --api-token "$TOKEN"
   ```

   Prints RESULT: PASS/FAIL (injects "HI" strokes, clicks Ask, checks answer + history overlay).

4. API probes: curl with `-H "X-Api-Token: $TOKEN"`; useful no-LLM surfaces are
   `POST /api/search` (typed `{"q": ...}`), `POST /api/digest?days=7`, `GET /api/export/<doc_id>.md`.

## Gotchas

- `server/.env` has `RM_HOST=192.0.2.1` (placeholder): syncs fail in ~3s (ssh ConnectTimeout=3)
  and degrade to cached data with `sync_error` set - this is the expected offline behavior,
  not breakage.
- `/api` routes 401 without the token; `/healthz` and `/metrics` are open.
- Asks cost real LLM calls (transcribe + answer) against the claude.ai subscription; refresh
  also triggers a one-time background session warm-up (one seed call per document).
- QML syntax check without running anything: `uvx --from pyside6-essentials python scripts/check-qml.py`.
- The simulator regenerates `device-app/ui/Config.qml` (gitignored) from `Config.qml.in`.
