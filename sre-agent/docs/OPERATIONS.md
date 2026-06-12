---
id: operations
slug: /operations
title: Operations & Release
sidebar_position: 5
description: Day-2 operations — building & publishing to a private registry, pull-based GitOps delivery with Flux + SOPS, GPU inference, observability, backup/restore, and the release checklist.
---

# Operations & Release

Everything past the first install: how you build and ship Keos to a fleet, run
it air-gapped, observe it, back it up, and release new versions. All commands
are `keosctl`.

## Private registry (the fleet flow)

For the security/air-gapped posture, images and the OCI Helm chart live only in
a local `:5000` registry. Populate it from a connected workstation:

```bash
export REGISTRY=registry.fleet.local:5000
keosctl release all [--insecure]     # build multi-arch images + mirror third-party + push chart + audit
```

`release all` runs four steps (also available individually): `build` (multi-arch
first-party + the Jetson ollama variant), `mirror` (copy pinned third-party
images, multi-arch preserved via skopeo), `chart` (push `oci://<reg>/charts/pocteo`),
`verify` (multi-arch audit). Build the arm64 leg on **native arm64 hardware** —
`uv pip install` segfaults under QEMU emulation.

**Trust the registry on each node** (k3s example, `/etc/rancher/k3s/registries.yaml`):

```yaml
mirrors:
  registry.fleet.local:5000:
    endpoint: ["https://registry.fleet.local:5000"]
configs:
  registry.fleet.local:5000:
    tls: { ca_file: /etc/keos/registry-ca.crt }   # or, for plain-HTTP: insecure_skip_verify: true
```

Then install from it: `keosctl install --headless --image-registry $REGISTRY [--plain-http]`.

## Air-gap bundle

For sites with no network at all, build one tarball on a connected machine:

```bash
keosctl bundle create                 # images + chart + pre-pulled models
```

Carry it in, load the images into the local registry, and
`keosctl install --headless --airgap --image-registry <reg> --plain-http`.

## GitOps (pull-based delivery)

The recommended day-2 model for air-gapped fleets: the cluster runs **Flux** and
**pulls** desired state from `:5000` — nothing is pushed in. Secrets are
SOPS-encrypted in the config artifact and decrypted in-cluster with an age key.

```
workstation                                   edge cluster
keosctl release all     → images + chart  →  :5000  ←  Flux source-controller
keosctl release config  → keos-config art →  :5000        ↓
                                                      kustomize-controller (sops→age)
                                                      → HelmRelease → helm-controller → install/upgrade
```

**One-time fleet setup** (workstation):

```bash
age-keygen -o keos-fleet.agekey            # private key — keep offline
export KEOS_AGE_RECIPIENT=$(grep 'public key' keos-fleet.agekey | awk '{print $4}')
```

**Publish desired state:**

```bash
keosctl gitops render --registry $REGISTRY --env edge        # → values-edge.yaml (review it)
ENV=edge VALUES_FILE=values-edge.yaml keosctl release config [--insecure]
```

**Bootstrap the cluster (once):**

```bash
keosctl gitops bootstrap --registry $REGISTRY \
    --age-key ./keos-fleet.agekey --config-tag 1.1.0-edge [--plain-http]
keosctl gitops status        # Flux reconciling Keos from :5000
```

**Day-2 update — pull, no push:** bump the chart/values upstream →
`keosctl release config` re-pushes → `keosctl gitops sync` (or wait the
interval). The change lands with **zero `kubectl`/`helm` against the cluster**.
Roll back by re-pushing a previous config tag. Pause with
`keosctl gitops suspend`.

## GPU inference

On a GPU node, confirm the LLM is actually offloaded (not silently on CPU):

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'   # → 1
keosctl shell ollama -- ollama ps          # PROCESSOR must read 100% GPU
keosctl shell ollama -- ollama ps | grep -i gpu
```

On a Jetson Orin, watch `sudo tegrastats` GR3D spike during a `keosctl run`.
Expect tens of tok/s and a full incident in seconds (vs. minutes on CPU). If
`ollama ps` says CPU, the Jetson ollama variant / `JETSON_JETPACK=6` env isn't
applied — re-run `keosctl install --gpu`, or fall back to `--no-gpu`.

## Observability

- **Metrics** — Prometheus metrics for the agent loop, safety mesh, and event
  bus are exposed; a ServiceMonitor ships when `monitoring.enabled=true`.
- **Logs** — structured JSON on every component; `keosctl logs <component> -f`.
- **Live** — `keosctl top` (one-screen dashboard), `keosctl events -w`,
  `keosctl bus tail` (raw event stream), the SPA's Pipeline view.
- **Audit** — `keosctl audit tail` / `keosctl audit verify` (HMAC chain).

## Backup & restore

```bash
keosctl backup -o keos-backup.tar.gz       # audit chain + vector store + values + AgentMode CR
keosctl restore keos-backup.tar.gz
```

Add `--include-models` to capture the Ollama blobs (large). Retention windows
for audit / incidents / traces are Helm values. For full DR, see the
[disaster-recovery runbook](./runbooks/disaster-recovery.md).

## Release checklist

1. Bump `deploy/helm/pocteo/Chart.yaml` + first-party image tags; update `CHANGELOG.md`.
2. `REGISTRY=<reg> keosctl release all` → 0 audit failures (amd64 + arm64; opa `-static`).
3. Tier-1 box (amd64, BTF): `keosctl preflight` green → `keosctl install --image-registry <reg>`
   → `keosctl doctor` green → `keosctl doctor --e2e` (shadow-read becomes an incident)
   → `keosctl run lib-reverse-shell` (quarantine + deny-all NetworkPolicy).
4. Jetson (arm64, GPU): GPU setup → `keosctl falco-probe build` → install `--gpu`
   → `ollama ps` 100% GPU → `keosctl run lib-reverse-shell` contained < 90 s.
5. Upgrade idempotency: re-run install / `keosctl upgrade` → values + the falco
   prebuilt-probe survive. `keosctl uninstall` → clean (no orphaned netpol/labels).

## Troubleshooting

`keosctl doctor` is the first stop — it checks every component and prescribes
the fix. For specific failure modes (api/ollama/nats down, audit tampering,
runaway graph, drift storm, agent took a destructive action, secret rotation,
DR) see the [Runbooks](./runbooks/README.md).
