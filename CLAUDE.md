# Remarque - agent notes

AI reading assistant for the reMarkable Paper Pro: Python FastAPI companion server (`server/`) + QML AppLoad panel (`device-app/`) running inside xochitl via xovi.
This file holds hard-won operational knowledge; trust it before re-investigating.

## Dev workflow (Mac)

- Server: `cd server && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000` (the tablet points at `http://10.11.99.2:8000`).
- Tests: `cd server && .venv/bin/python -m pytest tests/ -q` (fast, all offline).
- QML syntax check: `uvx --from pyside6-essentials python scripts/check-qml.py device-app` (also `spike`).
- Full E2E without or with the tablet: follow `.claude/skills/verify/SKILL.md` (seeded doc + isolated `REMARQUE_DATA_DIR` + simulator).
  Export `RM_HOST=192.0.2.1` for a deterministic offline run; with the real `RM_HOST` reachable the "isolated" server rsyncs the entire real tablet library.
- Simulator E2E: `uvx --from pyside6-essentials python scripts/simulator.py --test --server-url http://127.0.0.1:8123 --api-token "$TOKEN"`.
  It asserts: answer received, answers open on page 1, dot strokes register, history list + detail flow, question in history.
- Secrets live in `server/.env` (gitignored). Extract without printing: `TOKEN=$(grep '^API_TOKEN=' server/.env | cut -d= -f2-)`.
- Gotcha: a Colima/Docker container from `docker compose` may hold port 8000 and shadow the dev server (`docker compose down`; it returns if Colima restarts).

## Deploy to tablet

- App: `SERVER_URL=http://10.11.99.2:8000 API_TOKEN="$TOKEN" DEVICE_HOST=10.11.99.1 ./scripts/deploy-app.sh device-app`.
- AppLoad caches app resources in-process: ALWAYS `systemctl restart xochitl` on the tablet after deploying, or changes silently do not appear.
- Server changes: restart the uvicorn on port 8000 (no auto-reload).
- xochitl restart drops USB networking for ~10-30 s; never assume one ssh round-trip succeeds - use an `until ssh ...; do sleep 5; done` loop with a timeout.

## Tablet access and xovi

- SSH: `ssh -i ~/.ssh/remarkable root@10.11.99.1` (USB; the Mac is 10.11.99.2). BusyBox userland: no `timeout`, `head -N`/`grep -A` quirks (prefer `sed -n`).
- Stack: xovi (LD_PRELOAD into xochitl) + rm-appload (windowed QML apps) + qt-resource-rebuilder/qmldiff (needs the hashtab at `/home/root/xovi/exthome/qt-resource-rebuilder/hashtab`; missing hashtab = xochitl ABRT crash loop).
- Apps live under `/home/root/xovi/exthome/appload/`; ours is `ai-reading-assistant`.
- Verify xovi injection: `grep -c xovi /proc/$(pidof xochitl | cut -d" " -f1)/maps` (nonzero = injected). `pgrep -f xovi` is a FALSE POSITIVE (matches itself).
- Filesystem: `/` is read-only, `/etc` is a volatile overlay (RAM) recreated every boot; only `/home` and `/data` persist.
  To write the REAL `/etc` (persists): `mount -o remount,rw / && umount -R /etc`, write, `sync`, reboot (the overlay comes back on boot). This is how remagic's tripletap persists too.
- Boot persistence is SOLVED: `xovi-autostart.service` (in the real `/etc`) re-runs `/home/root/xovi/start` after boot (`RequiresMountsFor=/home/root/xovi` is essential - encrypted /home mounts late).
  Kill switch: `touch /home/root/xovi/disable-autostart`. Manual start: `systemd-run --unit=xovi-start-$(date +%s) bash /home/root/xovi/start` (systemd-run because the restart drops the ssh connection).
  Backup trigger: triple-press the power button (xovi-tripletap, installed).
- An OS update wipes the autostart unit and requires `remagic setup` re-run.

## Device app facts (verified on hardware - do not re-litigate)

- The pen reaches AppLoad QML as SYNTHESIZED MOUSE events at full digitizer rate (150-800 pts/stroke measured). There are NO touch, stylus, or tablet events at the app level: PointHandler and MultiPointTouchArea capture nothing (both tested; MPTA even degraded feel - reverted).
- The first ~20 px of EVERY stroke are eaten upstream in xochitl's input pipeline (constant firstGap≈20 in logs). Unfixable app-side; patching GesturesWindow's swipe filters did NOT fix it. Mitigation: server-side Catmull-Rom smoothing in `inkrender.py`.
- The Marker's tail eraser is indistinguishable from the tip at app level (no button, no pointer type - verified via evtest: the kernel DOES expose BTN_TOOL_RUBBER, xochitl just flattens it). Hence the panel's Erase-mode toggle.
- e-ink rendering: xochitl does NOT rasterize QtQuick.Shapes - use Canvas (`renderStrategy: Immediate`, `renderTarget: Image`, incremental `markDirty` per segment). Canvas MUST `repaintAll()` on width/height change or it shows a stale ghost frame.
- Writing latency needs `DisplayMethodArea { displayMethod: UFast }` (UFast ink looks slightly faint/rough - hardware trade).
- The AppLoad window chrome already has minimize (`_`), maximize (`□`), drag, and resize - do not build in-app window controls.
- Per-stroke instrumentation logs `remarque ink: pts=N ms=T firstGap=G` to the xochitl journal - read with `journalctl -u xochitl | grep "remarque ink"`.
- xochitl saves page ink lazily (page turn/close/suspend); `saveMyNeck()` exists on the MainWindow but the panel could not reach a working save trigger - Page ask retries and tells the user to flip the page when ink is stale.
- Qt Repeater delegates live in the VISUAL tree: find them via `childItems()` traversal, not `findChildren()` (simulator does this). Delegates need explicit `required property var modelData / int index`.

## Server facts

- Two-stage asks: cheap vision transcribe → strong answer in a persistent per-document session (claude-code provider, subscription auth).
  Session seeded ONCE with doc text capped at `MAX_DOC_CHARS` (150k chars ≈ 300-400 pages); follow-ups resume by id and are mostly cache reads. Content past the cap is invisible - that is what Attach: page/full is for; Attach: image is the only way Claude sees figures.
- History pages are 1-based PDF pages; `/api/refresh` returns the matching `page_number` (notebook `page` is 0-based - different unit).
- rsync from tablet uses `-a` WITHOUT `-z` (compression is tablet-CPU-bound: 90 s+ stalls vs ~20 s for 413 MB without).
- `inkrender.py` renders 2x supersampled + LANCZOS downscale + thin pen (small cursive loops must stay open) + `_smooth` Catmull-Rom.
- The transcribe prompt primes for English question phrasing; escalation path for misreads: Re-read button (strong model), then `CLAUDE_CODE_TRANSCRIBE_MODEL` bump.

## xochitl reverse-engineering toolkit (when needed)

- Keep a copy of the xochitl binary for analysis (`scp` from `/usr/bin/xochitl`).
- Embedded QML extraction: scan the binary for zstd frames (`\x28\xb5\x2f\xfd`), pipe each through `zstd -d -c`, keep blobs containing `import Qt` (some files are also plaintext).
- qmldiff patches: build the CLI from github.com/asivery/qmldiff (`cargo build --release`); test diffs OFFLINE with `qmldiff apply-diffs <root> <dest> <diff.qmd>` against the extracted QML before touching the device.
  QMD gotchas: statements must be FLAT (no leading indentation - indented TRAVERSE fails to parse), `[.prop~substring]` (contains) matches reliably where exact `=` with escaped quotes does not, `REPLACE x WITH { x: ... }` sets its own cursor, `INSERT` needs `LOCATE` first.
- External diffs: drop a `.qmd` into `/home/root/xovi/exthome/qt-resource-rebuilder/` and restart xochitl; errors appear in the journal as `[qmldiff]: ...`. Rollback = delete the file + restart.

## Known device issue - top input band (UNRESOLVED, do not re-investigate unless asked)

- The top ~10% of the screen (portrait) ignores ALL input (pen + finger) system-wide; display is fine.
- Established: kernel/digitizers report clean coordinates in the band (evtest on event2=pen "Elan marker input", event3=touch "Elan touch input"); xochitl-level UI never reacts; the band predates all our modifications; survives reboots AND a factory reset; zeroing GesturesWindow's swipe-filter areas via qmldiff did NOT help (the eater is deeper, in reMarkable's C++ input routing or controller firmware).
- Untested lead: whether the controller reports CONTACT (pressure/BTN_TOUCH) in the band or only hover coordinates - would distinguish hardware contact-layer defect (warranty ammo) from OS bug.
- User has parked this; next steps if resumed: contact-state capture, OS update, reMarkable support.

## Conventions

- Never print secrets; `server/.env` holds `API_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`, `RM_HOST`.
- Commits: no agent co-author trailer (user rule). Verify before deploy: pytest + check-qml + simulator E2E.
- The user validates on the hardware; deploy + restart xochitl, then ask them to test, and read the journal for the instrumentation.
