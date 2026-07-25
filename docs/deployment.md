# Deployment

## Docker Compose

The simplest self-hosted setup: one container, state in a named volume.

```bash
cd server && cp .env.example .env
# in server/.env set at least:
#   CLAUDE_CODE_OAUTH_TOKEN (from: claude setup-token)
#   RM_HOST (tablet LAN IP), API_TOKEN
cd .. && docker compose up -d
```

The tablet then talks to `http://<host-ip>:8000`.
To sync documents from the tablet, uncomment the SSH-key mount in `compose.yaml` (the startup command fixes the key permissions ssh requires).

Optional profiles (the env settings each needs are listed at the top of `compose.yaml`):

- `--profile anki` runs the in-container Anki for AnkiWeb sync (log in once at `http://localhost:3000`).
- `--profile ollama` runs a local vision model for transcription.

## Kubernetes

The server can run in the cluster instead of a Mac; the cluster and tablet share the LAN, which is the one hard requirement (the pod SSHes to the tablet, the tablet HTTPs to the pod).

This repo never deploys anything: GitHub Actions (`.github/workflows/ci.yml`) only tests and publishes the server image to GHCR (`ghcr.io/rajagopal2000/remarque-server`) on pushes to main.
`deploy/kubernetes/` is a reference chart to vendor into the GitOps repo that owns the cluster.

1. For a private repo, add the GHCR pull secret per `deploy/kubernetes/SECRETS.md`.
2. Copy `deploy/kubernetes/` to `applications/remarque/` in the kubernetes-infrastructure repo (app dir = namespace = release); ArgoCD reconciles from there.
3. Create the two sealed secrets per `deploy/kubernetes/SECRETS.md`: the Claude subscription token (`claude setup-token` on the Mac) and a dedicated tablet SSH key.
4. Pick a free MetalLB IP for `serviceIP` and set `config.rmHost` to the tablet's reserved LAN IP.
5. Deploy the device app with `SERVER_URL=http://<serviceIP>:8000`.

### State

Synced documents, sqlite sessions and history, Claude Code session transcripts, and ssh known_hosts live on one Longhorn PVC mounted at `/data`; the deployment uses `Recreate` so there is never a second writer.

### In-cluster Anki

The chart runs an Anki container (`chrislongros/anki-desktop`, KasmVNC + AnkiConnect) as a ClusterIP-only sidecar service holding your AnkiWeb login on its own PVC; the server syncs down, adds notes, and syncs up on every deck.
One-time setup after the first deploy: port-forward the web UI and log in to AnkiWeb (see `deploy/kubernetes/SECRETS.md`).

### Caveats

- The subscription token needs resealing about yearly.
- The codex provider is not wired for in-cluster auth (its auth.json rotates refresh tokens), so use `claude-code` in the cluster.
