# Setup for remarque

## One-time Anki login (no sealed secret)

The in-cluster Anki container holds the AnkiWeb credentials in its profile on
the `remarque-anki-config` PVC; nothing is committed to git.
After the first deploy:

```bash
kubectl -n remarque port-forward svc/remarque-anki 3000:3000
```

Open http://localhost:3000, then in the Anki UI: sync (cloud icon), log in with
your AnkiWeb account, and confirm the AnkiConnect add-on is active (the server
reaches it at http://remarque-anki:8765).
The login persists on the PVC across restarts.

## GHCR image pull (only if the GitHub repo is private)

Public GHCR images need no pull secret.
For a private image, create a GitHub PAT with `read:packages`, then:

```bash
kubectl create secret docker-registry ghcr-pull \
  --namespace remarque \
  --docker-server=ghcr.io \
  --docker-username=Rajagopal2000 \
  --docker-password='<PAT with read:packages>' \
  --dry-run=client -o yaml \
  | kubeseal --controller-namespace sealed-secrets -o yaml \
  > templates/ghcr-pull-sealed.yaml
```

Then set `imagePullSecret: ghcr-pull` in `values.yaml`.

# Sealed secrets for remarque

Two secrets are needed.
Create them with kubeseal on a machine with cluster access and commit only the sealed output as `templates/<name>-sealed.yaml` (never the plaintext).
Namespace is `remarque` (app dir = namespace convention).

## 1. Claude subscription token (remarque-auth)

Generate a long-lived OAuth token from your logged-in Claude Code on the Mac:

```bash
claude setup-token
```

Then seal it:

The same secret also carries the API token the tablet must present (bake the identical value into the device app with `API_TOKEN=... ./scripts/deploy-app.sh device-app`):

```bash
kubectl create secret generic remarque-auth \
  --namespace remarque \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN='sk-ant-oat01-...' \
  --from-literal=API_TOKEN="$(openssl rand -hex 24)" \
  --dry-run=client -o yaml \
  | kubeseal --controller-namespace sealed-secrets -o yaml \
  > templates/remarque-auth-sealed.yaml
```

The OAuth token lasts about a year; re-run `claude setup-token` and reseal to rotate.

## 2. Tablet SSH key (remarque-ssh)

Use a dedicated keypair; put the public half in the tablet's `~/.ssh/authorized_keys`.

```bash
ssh-keygen -t ed25519 -f id_remarkable -N "" -C remarque
ssh-copy-id -i id_remarkable.pub root@<tablet-ip>

kubectl create secret generic remarque-ssh \
  --namespace remarque \
  --from-file=id_remarkable=id_remarkable \
  --dry-run=client -o yaml \
  | kubeseal --controller-namespace sealed-secrets -o yaml \
  > templates/remarque-ssh-sealed.yaml

rm id_remarkable id_remarkable.pub
```
