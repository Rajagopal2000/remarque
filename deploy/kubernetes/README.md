# Deployment template (not applied from this repo)

This directory is a reference Helm chart, not a deployment pipeline.
This repo's CI only builds and publishes the server image to GHCR; it holds no cluster credentials and never touches Kubernetes.

To deploy: copy this directory into the GitOps repo that owns the cluster (as `applications/remarque/`, following the app-dir-equals-namespace convention), create the sealed secrets per `SECRETS.md`, adjust `values.yaml` (image tag, MetalLB IP, tablet IP), and let ArgoCD reconcile it.
Version bumps happen by editing `values.yaml` in the GitOps repo to point at a new image SHA from GHCR.
