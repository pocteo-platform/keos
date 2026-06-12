# GitOps delivery (Flux + OCI config artifact + SOPS) — Phase 23

Pull-based, air-gapped, private-registry-only delivery. The edge cluster runs
**Flux** (source + helm + kustomize controllers). Nothing is pushed into the
cluster — Flux **pulls** the desired state from the private `:5000` registry
and reconciles it.

```
workstation (connected, git-versioned)            edge cluster (air-gapped)
release.sh all     → images + OCI chart  ─push→ :5000 ←pull─ Flux source-controller
release.sh config  → keos-config artifact ─push→ :5000        │ OCIRepository/keos-config
                                                              ▼
                                       kustomize-controller (sops-decrypt via age key)
                                         → applies OCIRepository(chart) + HelmRelease + Secret
                                                              ▼
                                       helm-controller → pulls oci://:5000/charts/pocteo → installs/upgrades
```

**Update a release** = bump the chart version and/or `values-<env>.yaml`,
`scripts/release.sh config` re-pushes the `keos-config` artifact to `:5000`,
and Flux reconciles on its interval (or `keosctl gitops sync`). No
`helm upgrade`, no `kubectl` against the cluster.

## Layout

| Path | Applied by | Purpose |
|---|---|---|
| `flux/ocirepository-config.yaml` | `install-edge.sh --gitops` (bootstrap, once) | Root `OCIRepository/keos-config` → `oci://<reg>:5000/keos-config:<tag>` |
| `flux/kustomization.yaml` | bootstrap, once | Root `Kustomization/keos` (sourceRef=keos-config, `decryption: sops`) |
| `config/kustomization.yaml` | Flux (from the artifact) | kustomize index of the artifact contents |
| `config/ocirepository-chart.yaml` | Flux | `OCIRepository/keos-chart` → `oci://<reg>:5000/charts/pocteo:<ver>` |
| `config/helmrelease.yaml` | Flux | `HelmRelease/keos` (chartRef=keos-chart, values from `values-<env>.yaml`) |
| `config/secret.sops.yaml` | Flux (after SOPS decrypt) | the `keos-pocteo-auth` Secret — **encrypted at rest** |
| `config/values-<env>.yaml` | merged into the HelmRelease by `release.sh config` | per-cluster values (registry, GPU, mode, security tier, node pinning) |
| `.sops.yaml` | `sops`/`release.sh config` | encrypt rule: only `data`/`stringData`, recipient = fleet age key |

The `*.yaml` here under `flux/` and `config/` are **templates** with
`${REGISTRY}`, `${VERSION}`, `${CONFIG_TAG}`, `${OCI_INSECURE}` placeholders.
`release.sh config` (for `config/`) and `install-edge.sh --gitops` (for
`flux/`) substitute them at build/bootstrap time.

## One-time fleet setup (workstation)

```bash
age-keygen -o keos-fleet.agekey          # the fleet private key — keep safe, offline
grep 'public key' keos-fleet.agekey      # → age1... (the recipient; export as KEOS_AGE_RECIPIENT)
```

- **Public key** (`KEOS_AGE_RECIPIENT`): encrypts secrets at `release.sh config` time.
- **Private key** (`keos-fleet.agekey`): installed on each cluster at bootstrap as the
  `sops-age` Secret in `flux-system`; only the cluster can decrypt.

Prereqs on the build workstation: `flux`, `sops`, `age`, `yq` (in addition to
`docker`/`helm`/`skopeo` the rest of `release.sh` needs).

See `docs/OPERATIONS.md` for the full day-2 workflow, key rotation, and rollback.
