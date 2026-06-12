# Release & fleet-rollout checklist

The manual e2e matrix that replaces CI (no pipeline yet — see P2 below). Run it
from a workstation with a populated fleet registry before declaring a release
good. Two reference devices: an amd64 k3s box (Tier 1, BTF) and a Jetson Orin
(Tier 2, no BTF).

## 1 · Build & publish (workstation)

- [ ] `docker run --privileged --rm tonistiigi/binfmt --install arm64` (once)
- [ ] bump `deploy/helm/pocteo/Chart.yaml` version if changed
- [ ] `make release REGISTRY=<host:port> [INSECURE=1]` → builds multi-arch
      first-party + Jetson ollama variant, mirrors third-party, pushes chart
- [ ] `make audit-images REGISTRY=<host:port>` → **0 failures** (amd64+arm64
      everywhere; opa `-static`; no hardcoded arch in Dockerfiles)

## 2 · Fresh amd64 k3s (Tier 1)

- [ ] device trusts the registry (`/etc/rancher/k3s/registries.yaml`, `docs/REGISTRY.md`)
- [ ] `keosctl preflight --image-registry <reg>` → all green, tier=modern_ebpf
- [ ] `bash install-edge.sh --image-registry <reg>` → 15/15 pods, **no** `--local-build`, no ghcr.io pulls
- [ ] `keosctl doctor` → all green; tetragon Running
- [ ] `keosctl doctor --e2e` → shadow-read becomes an incident ≤60s
- [ ] `keosctl run lib-reverse-shell` → quarantine label + deny-all netpol on nginx-demo

## 3 · Fresh Jetson Orin (Tier 2)

- [ ] `keosctl preflight --gpu --image-registry <reg>` → GPU+Tegra remediation, btf=false, memory warn
- [ ] `sudo keosctl gpu-setup --apply` → `nvidia.com/gpu` allocatable = 1
- [ ] `sudo REGISTRY=<reg> scripts/build-falco-probe.sh` (once per kernel)
- [ ] `bash install-edge.sh --image-registry <reg> --gpu` → tier=ebpf-prebuilt
- [ ] `kubectl -n keos exec ollama-0 -- ollama ps` → **100% GPU** (or CPU/GPU split, not CPU-only)
- [ ] `keosctl run lib-reverse-shell` → contained **<90s** (the headline criterion)
- [ ] `keosctl doctor --e2e` → falco probe loaded, shadow-read → incident

## 4 · Upgrade idempotency (both)

- [ ] `keosctl upgrade --chart-version <new>` (or re-run install) → all values + the
      falco prebuilt-probe initContainer **survive** (regression: the old
      live `kubectl patch` did not)
- [ ] `keosctl uninstall` → clean (no orphaned netpol/labels in `workloads`)

---

## P2 follow-up tickets (sketched, not yet implemented)

These were scoped out of the P0+P1 hardening. File as issues.

### Installer robustness (`install-edge.sh`)
- `--repair`: when the last helm release is `failed`, `helm uninstall --wait`
  then reinstall (today a failed first release strands the operator — report §1.3-3).
- Scope `--wait`: core components via helm `--wait`; security DaemonSets via a
  per-component `kubectl rollout status` loop with actionable messages
  ("falco probe missing for kernel X — see docs/SECURITY_SENSORS.md") instead
  of one opaque 10-minute timeout.
- `--resume-postinstall`: factor model-pull / demo-workloads / RAG-seed into
  idempotent functions; resume only those (an OOM mid-pull currently kills the
  script and the remaining steps silently never run — report §1.5).
- Model-pull resilience: pull the small embed model first, retry the chat model
  once on exit 137 with a swap hint.

### Other platforms (registry-only — `--local-build` impossible)
- **Talos**: BTF present → Tier 1; GPU via NVIDIA **system extensions** in the
  machine config (installer verify-only, no host access); **no swap** → the
  preflight memory check hard-gates the 7b model. Installer should *say*
  `--local-build` is unavailable instead of dying on missing docker.
- **Kairos (Jetson)**: prebuilt falco probes are mandatory (immutable OS, no
  toolchain); CDI spec + tegra alias + nvidia toolkit must be **baked into the
  Kairos image recipe** (extend `docs/platforms/kairos-jetson.md`), not applied
  post-install.

### CI (when runners exist)
- buildx multi-arch for first-party + Jetson ollama + per-kernel falco probes,
  chart push on tag.
- the static guards as a gate: `audit-images.sh` + the Dockerfile arch lint.
- e2e matrix: kind (amd64) + a self-hosted Jetson runner (arm64+GPU) running
  this checklist's sections 2–3 automatically.

### Cosmetic
- Incident `proposed_tool` shows `page_human` even after a successful
  `isolate_pod` (confusing in API/SPA). report §1.7.
