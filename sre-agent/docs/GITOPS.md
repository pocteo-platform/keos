# GitOps delivery — Flux + private registry + SOPS (air-gapped)

Keos's day-2 update model for the **private-registry, air-gapped** posture.
The edge cluster runs **Flux** and **pulls** its desired state from your local
`:5000` registry — nothing is ever pushed into the cluster. Updating a release
is a workstation push + a reconcile; no `helm upgrade`, no inbound access.

```
workstation (connected, git-versioned)            edge cluster (air-gapped)
release.sh all     → images + OCI chart  ─push→ :5000 ←pull─ Flux source-controller
release.sh config  → keos-config artifact ─push→ :5000        │ OCIRepository/keos-config
  = OCIRepository(chart) + HelmRelease(values)                ▼
    + SOPS-encrypted Secret                  kustomize-controller (sops-decrypt via age key)
                                               → applies HelmRelease + chart source + Secret
                                               ▼
                                             helm-controller → pulls the chart from :5000 → installs/upgrades
```

Why this shape: one artifact store (`:5000`) for images, chart, **and** config;
no separate git server to run/secure inside the air-gap (the config is still
git-versioned **upstream** on the workstation); secrets are encrypted at rest
and only the cluster can decrypt. See `deploy/gitops/README.md` for the file
layout and `docs/REGISTRY.md` for registry trust.

## Prerequisites

| Where | Tools |
|---|---|
| Workstation (build) | the `release.sh` set (`docker`, `helm`, `skopeo`) **plus** `flux`, `sops`, `age`, `yq`, `envsubst` |
| Edge cluster | k3s/Talos/etc. already trusting `:5000` (`/etc/rancher/k3s/registries.yaml`, see `docs/REGISTRY.md`); the `flux` CLI on whatever drives `keosctl gitops` |
| Flux | **≥ 2.3** (HelmRelease `chartRef` → OCIRepository). Controller images are mirrored to `:5000/fluxcd` by `release.sh` |

## One-time fleet setup

Generate the fleet age keypair (once, kept offline):

```bash
age-keygen -o keos-fleet.agekey            # PRIVATE key — guard it
grep 'public key' keos-fleet.agekey        # → age1...  (the recipient)
export KEOS_AGE_RECIPIENT=age1...
```

- **Public key** (`KEOS_AGE_RECIPIENT`) encrypts secrets when you build config.
- **Private key** (`keos-fleet.agekey`) is installed on each cluster at
  bootstrap as the `sops-age` Secret; only the cluster can decrypt.

## Workstation — publish images, chart, and config

```bash
export REGISTRY=registry.fleet.local:5000

# images + multi-arch chart + Flux controller images (+ audit)
make release REGISTRY=$REGISTRY [INSECURE=1]        # = release.sh all

# render a per-cluster values file (probe a reachable cluster, or copy the example)
keosctl gitops render --registry $REGISTRY --env edge        # → values-edge.yaml
#   review: security tier, containerd socket (Talos differs), GPU, defaultMode

# build + SOPS-encrypt + push the desired-state artifact
ENV=edge VALUES_FILE=values-edge.yaml KEOS_AGE_RECIPIENT=$KEOS_AGE_RECIPIENT \
  REGISTRY=$REGISTRY scripts/release.sh config [--insecure]
#   → oci://$REGISTRY/keos-config:1.1.0-edge   (tokens generated once + cached
#     under ~/.keos-gitops/edge.env — back that up)
```

## Edge cluster — bootstrap (once)

```bash
keosctl gitops bootstrap \
    --registry registry.fleet.local:5000 \
    --age-key ./keos-fleet.agekey \
    --config-tag 1.1.0-edge \
    [--plain-http]
```

This installs Flux (controllers pulled from `:5000/fluxcd`), drops the
`sops-age` secret, and applies the root `OCIRepository/keos-config` +
`Kustomization/keos`. Flux then pulls everything else and installs Keos.

```bash
keosctl gitops status      # sources / kustomizations / helmreleases
keosctl gitops sync        # force an immediate reconcile (else it polls)
```

`install-edge.sh --gitops --image-registry <reg> --age-key <key> --config-tag <tag>`
is an equivalent one-shot wrapper.

## Day-2: update a release (the whole point — pull, no push)

1. Bump what changed **upstream** (git): the chart version (`Chart.yaml` +
   `release.sh build/mirror/chart`) and/or `values-edge.yaml`.
2. Re-push the config artifact: `scripts/release.sh config` (same env). Tip:
   use a moving tag (e.g. `1.1.0-edge`) so the cluster's OCIRepository ref
   doesn't change, or bump `--config-tag` and re-point.
3. The cluster reconciles on its interval, or force it: `keosctl gitops sync`.

No `kubectl`/`helm` is ever run against the cluster. The change is git-auditable
and revertible.

## Secrets (SOPS + age)

- The auth Secret (`SHP_API_TOKEN`, `AUDIT_HMAC_KEY`, `FALCO_WEBHOOK_TOKEN`)
  ships **encrypted** in the config artifact; the chart consumes it via
  `auth.existingSecret` (so Helm never templates the plaintext).
- **Rotate a token:** edit `~/.keos-gitops/<env>.env` (or delete it to
  regenerate), re-run `release.sh config`, `keosctl gitops sync`. The new
  Secret rolls out and the api/mcp pods restart to pick it up.
- **Rotate the age key:** generate a new keypair, re-encrypt
  (`release.sh config` with the new `KEOS_AGE_RECIPIENT`), then
  `keosctl gitops bootstrap … --age-key <new>` (re-creates `sops-age`).

## Suspend / resume / rollback

```bash
keosctl gitops suspend     # pause reconciliation (manual maintenance window)
keosctl gitops resume
# rollback: re-push a previous keos-config tag (or point --config-tag back),
# then `keosctl gitops sync`. Git upstream holds the history.
```

## Troubleshooting

| Symptom | Check |
|---|---|
| `flux push artifact` fails on plain-HTTP `:5000` | flux push needs the registry on HTTPS or a docker/containerd insecure-registries entry; mirror the registry behind TLS or use a self-signed CA (`certSecretRef` in the OCIRepositories) |
| HelmRelease `not ready` | `keosctl gitops status`; `kubectl -n flux-system describe helmrelease keos`; confirm the chart tag exists at `oci://<reg>/charts/pocteo` |
| Secret not decrypting | the `sops-age` Secret in `flux-system` must hold the PRIVATE key that matches the `KEOS_AGE_RECIPIENT` used at `release.sh config` time |
| Images ImagePullBackOff | the node must trust `:5000` (`registries.yaml`); `release.sh mirror` must have run for this version |
| Flux pods ImagePullBackOff | `release.sh` must have mirrored `:5000/fluxcd/*`; bootstrap used `--registry <reg>/fluxcd` |

## Relationship to the agent's drift detection (Phase 15)

Flux is **scheduled delivery** (reconciles on an interval). The agent's
Phase-15 drift detector is **real-time reactive** — it watches a source-of-truth
and can revert/triage drift between Flux reconciles. They compose: point
`DRIFT_SOURCE_PATH` at the reconciled manifests (or add a Flux/OCI
`SourceOfTruth` provider) so the agent reacts immediately while Flux owns the
desired state. See `docs/architecture/gitops-drift.md`.
