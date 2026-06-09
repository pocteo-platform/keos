---
id: ecosystem-registry
slug: /cli/ecosystem-registry
title: Ecosystem + registry
sidebar_position: 6
description: keosctl deps · images · values — install missing operators, swap the image registry, edit Helm values without yq.
---

# Ecosystem + registry

Three command groups that answer the everyday "what's actually installed on this cluster, and how do I change it" questions.

## `keosctl deps` — ecosystem operators

Keos itself ships **only `chaos-mesh`** as a sub-chart. Five other operators are detected at runtime and the agent adapts — but you can also install them on demand without leaving the CLI.

### `deps list`

```bash
$ keosctl deps list

Ecosystem operators
────────────────────────────────────────────────────────────────
  ✓ Chaos Mesh             detected
  ✗ Prometheus             not installed — baselines + forecasts disabled (Phase 13/14)
  ✗ kube-state-metrics     not installed — some baseline metrics will fail
  ✗ Argo CD                not installed — GitOps drift falls back to filesystem manifests
  ✗ Flux                   not installed — GitOps drift falls back to filesystem manifests
  ✗ cert-manager           not installed — Phase 19 mTLS will fall back to self-signed
```

Each ✗ row spells out **what feature is lost** when that operator is absent. If the impact doesn't matter for your install, leave it absent — Keos works.

### `deps install <name>`

```bash
keosctl deps install prometheus
keosctl deps install argocd
keosctl deps install cert-manager
```

Uses curated defaults per operator:

| Name | Repo | Chart | Default namespace |
|---|---|---|---|
| `prometheus` | `prometheus-community.github.io` | `kube-prometheus-stack` | `monitoring` |
| `argocd` | `argoproj.github.io/argo-helm` | `argo-cd` | `argocd` |
| `flux` | `oci://ghcr.io/fluxcd-community/charts` | `flux2` | `flux-system` |
| `cert-manager` | `charts.jetstack.io` | `cert-manager` (+ `installCRDs=true`) | `cert-manager` |
| `kube-state-metrics` | `prometheus-community.github.io` | `kube-state-metrics` | `monitoring` |

All flags pass through to `helm upgrade --install`:

```bash
# Internal Harbor chart-repo
keosctl deps install prometheus \
  --chart-repo https://harbor.corp/chartrepo/prometheus-community \
  --version 75.0.0

# Custom values file
keosctl deps install argocd --values argocd-prod.yaml

# Non-default namespace + ad-hoc --set
keosctl deps install cert-manager \
  --namespace cert-mgr \
  --set installCRDs=true \
  --set replicaCount=3
```

After installing, run:

```bash
keosctl restart api --wait     # so the agent re-discovers
keosctl deps list              # confirm
```

## `keosctl images` — registry + per-pod image management

The chart's `global.imageRegistry` defaults to `ghcr.io/pocteo-platform`. For corporate networks with internal Harbor / Nexus / JFrog, you can swap the registry without re-installing.

### `images list`

```bash
$ keosctl images list

Images currently running in ns/keos
────────────────────────────────────────────────────────────────
  api-cdfc5759b-b6hl6            ghcr.io/pocteo-platform/keos-api:1.0.1
  caddy-7899795d7-4swk6          caddy:2.8-alpine
  frontend-7ffbb6dccd-vr8c9      ghcr.io/pocteo-platform/keos-frontend:1.0.1
  mcp-server-68d7877b8c-2svc8    ghcr.io/pocteo-platform/keos-mcp-server:1.0.1
  nats-0                         nats:2.10.22-alpine
  ollama-0                       ollama/ollama:0.4.7
  opa-5497f44f56-6ngc6           openpolicyagent/opa:0.69.0
  qdrant-0                       qdrant/qdrant:v1.12.4
  chaos-controller-manager-…     ghcr.io/chaos-mesh/chaos-mesh:v2.7.2
  …
```

### `images registry`

```bash
$ keosctl images registry

Image registry configuration
────────────────────────────────────────────────────────────────
  › global.imageRegistry   ghcr.io/pocteo-platform
  › api pullPolicy         IfNotPresent
  › mcpServer pullPolicy   IfNotPresent
  › frontend pullPolicy    IfNotPresent
```

### `images set-registry <host>`

Swap the registry. Triggers `helm upgrade --reuse-values --set global.imageRegistry=<host>` + waits for the new pods to come up.

```bash
keosctl images set-registry registry.corp:5000      # corporate Harbor
keosctl images set-registry ghcr.io/pocteo-platform # back to public
```

### `images pull-policy <Always|IfNotPresent|Never>`

Override `pullPolicy` for the 3 first-party components (api, mcpServer, frontend).

```bash
keosctl images pull-policy Always         # force pull on every restart
keosctl images pull-policy Never          # local-build mode
keosctl images pull-policy IfNotPresent   # default
```

### Install-time alternative

If you know the registry at install time, pass it directly to the wizard or to `install-edge.sh`:

```bash
# Headless install
keosctl install --headless --image-registry registry.corp:5000

# Or directly
bash sre-agent/scripts/install-edge.sh --image-registry registry.corp:5000
```

This sets `global.imageRegistry` from the start — no `helm upgrade` needed afterwards.

## `keosctl values` — Helm values inspection + editing

A kubectl-style way to inspect and patch effective Helm values without leaving the CLI.

### `values get [key.path]`

```bash
# Full effective values
keosctl values get

# One key via dot notation (yaml-aware)
keosctl values get ollama.gpu.enabled
keosctl values get agent.defaultMode
keosctl values get baselines.prometheusUrl
```

### `values set <key>=<value>`

Patch one value via `helm upgrade --reuse-values --set`. Blocks until pods cycle:

```bash
keosctl values set agent.defaultMode=active
keosctl values set api.replicas=2
keosctl values set ollama.gpu.enabled=true
keosctl values set retention.auditDays=90
```

### `values edit`

Opens the full effective values yaml in `$EDITOR`. On save, runs `helm upgrade --reuse-values -f <tmp>`.

```bash
EDITOR=vim keosctl values edit
EDITOR=code keosctl values edit
```

### `values diff`

Unified diff of effective values vs the chart's defaults:

```bash
$ keosctl values diff
--- /dev/fd/63   chart defaults
+++ /dev/fd/62   effective values
@@ -1,491 +1,32 @@
-# Keos Helm chart values.
-#
-# Every knob lives here. …
-…
+auth:
+  apiToken: 1XLAnaz…
+  hmacKey: 8s2K…
+agent:
+  attachment:
+    mode: hard
+    nodeName: kubewarroom-control-plane
+  defaultMode: observe-only
…
```

## Common workflows

### "Install Prometheus + restart agent so baselines start learning"

```bash
keosctl deps install prometheus
keosctl restart api --wait
# Wait ~7 days for the learner to fill its history
keosctl values get baselines.prometheusUrl   # confirm wired
```

### "Migrate from public ghcr.io to corporate Harbor"

```bash
# 1. Mirror images into Harbor (one-time)
docker pull ghcr.io/pocteo-platform/keos-api:1.0.1
docker tag  ghcr.io/pocteo-platform/keos-api:1.0.1 registry.corp:5000/pocteo-platform/keos-api:1.0.1
docker push registry.corp:5000/pocteo-platform/keos-api:1.0.1
# (repeat for keos-mcp-server, keos-frontend)

# 2. Swap the registry on the running install
keosctl images set-registry registry.corp:5000/pocteo-platform

# 3. Verify
keosctl images list
```

### "Bump retention to 90 days"

```bash
keosctl values set retention.auditDays=90
keosctl values set retention.incidentDays=180
```

## See also

- [Install + lifecycle](./install-uninstall) — `install --image-registry` at install time
- [Operations](./operations) — `status`, `restart`, `upgrade`
- [Air-gapped install](../platforms/airgap.md) — the full no-internet path
