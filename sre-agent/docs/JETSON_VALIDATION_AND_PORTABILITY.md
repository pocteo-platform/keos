# Jetson Orin validation report & cross-platform install hardening plan

> **Date:** 2026-06-12 · **Device:** Jetson Orin Nano 8 GB (`pocteo`) · **Outcome:** all four
> "done" criteria from `docs/JETSON_SESSION_NOTES.md` met.
>
> Part 1 is the full session record: every problem hit, its root cause, and the exact fix
> applied (with file references). Part 2 is the proposal so the next `keosctl install`
> works first-try on any Linux distribution (Debian/Ubuntu/…) and any cluster flavour
> (k3s, Kairos, Talos, RKE2, kubeadm). Part 2 is the input for the engineering plan.

---

## Part 1 — What happened, from the beginning

### 1.0 Starting environment (recon)

| | |
|---|---|
| Board | Jetson Orin Nano, **8 GB unified RAM**, 233 GB NVMe (209 GB free) |
| OS | Ubuntu 24.04.4 LTS (desktop session running: GNOME + Firefox + VS Code ≈ 2.5 GB RAM) |
| L4T / JetPack | **R39.2** (JetPack 7 era — newer than every doc/tool assumed, which assumed R36/JetPack 6) |
| CUDA / driver | **CUDA 13.2**, driver **595.78** (`nvidia-smi` works) |
| Kernel | `6.8.12-1021-tegra` — out-of-tree NVIDIA build, **no BTF** (`/sys/kernel/btf/vmlinux` absent) |
| Pre-installed | docker, containerd, nvidia-ctk 1.19.1 · **missing:** k3s, helm, kubectl, ollama, keosctl |
| Swap | **0 B** (this mattered later) |

The single theme of the whole session: **R39.2 is ahead of the entire ecosystem** (ollama,
falco loader, device plugin, our own chart pins), and **the agent images had never been
built/run on ARM64**. Every failure below is one of those two stories.

### 1.1 k3s + kubeconfig — clean

`curl -sfL https://get.k3s.io | sh -` → k3s **v1.35.5+k3s1**, node Ready in ~90 s.
Copied `/etc/rancher/k3s/k3s.yaml` → `~/.kube/config` (k3s's kubectl symlink defaults to
the root-owned path; `KUBECONFIG` must be exported — added to `~/.bashrc`).

### 1.2 GPU into k3s — k3s auto-wired the runtime, but CDI needed surgery

* k3s ≥1.30 **auto-registers** the `nvidia` runtime in its containerd config when
  `nvidia-container-runtime` exists at startup — step 2 of the runbook's manual
  `nvidia-ctk runtime configure` was unnecessary.
* Applied the RuntimeClass + NVIDIA device plugin **v0.16.2** → `nvidia.com/gpu`
  allocatable = **1** immediately, no Jetson-specific patching.

**Problem A — ollama pod `StartError exit=128`:**
`OCI runtime create failed: … failed to generate CDI spec for mode "csv": unsupported
device id: tegra`.
**Root cause:** the toolkit auto-detects **CSV mode** on Tegra; the k8s device plugin
allocates the GPU under device id **`tegra`**, which the CSV/CDI generator in toolkit
1.19.1 doesn't know on R39.
**Fix (host-level):**
1. `sudo nvidia-ctk cdi generate --mode=csv --output=/etc/cdi/nvidia.yaml` (works offline —
   produces devices `0` and `all`).
2. **Manually append a `tegra` alias device** (copy of `all`) to `/etc/cdi/nvidia.yaml`.
3. `sudo nvidia-ctk config --in-place --set nvidia-container-runtime.mode=cdi`.
4. Restart the pod → Running.

### 1.3 The installer — three failures before a clean run

Ran `bash scripts/install-edge.sh --local-build --gpu` (OCI chart not anonymously
pullable → local-chart fallback, as designed).

| # | Failure | Root cause | Fix |
|---|---|---|---|
| 1 | Preflight `die`: helm missing | helm not preinstalled on the board | installed helm v3.21.0 (official script) |
| 2 | `docker … permission denied` | user not in `docker` group | `usermod -aG docker` + re-run under `sg docker` |
| 3 | `helm … --wait`: **context deadline exceeded** (10 m) | 4 pods crash-looping (below) blocked readiness; install died **before** model pull / demo workloads / RAG seed | fixed the pods, `helm uninstall`, re-ran installer |

The ARM64 image builds themselves worked fine (~15 min first time, seconds cached).

### 1.4 The four crash-looping pods (the heart of the session)

#### a) `opa` — `exec /opa: exec format error`
**Root cause:** `openpolicyagent/opa:0.69.0` bare tag is **amd64-only**.
**Fix:** `values.yaml` → `0.69.0-static` (the `-static` tags are multi-arch). Running
instantly after.

#### b) `tetragon` — fatal: BTF search failed
**Root cause:** hard requirement on kernel BTF; NVIDIA's L4T kernel is built without
`CONFIG_DEBUG_INFO_BTF`. **Not fixable in userspace.**
**Mitigation:** parked the DaemonSet behind a nodeSelector that matches nothing
(`values.yaml`: `tetragon.nodeSelector: {keos.pocteo/btf-kernel: "true"}`) so the chart
stays enabled, helm `--wait` stays green, and a future BTF-capable node picks it up by
just adding the label.

#### c) `falco` — two-layer failure
* First layer: `modern_ebpf` probe → `libpman: prog 'BPF_TRACE_RAW_TP' is not supported`
  → same BTF root cause. Switched `falco.driver.kind` → `ebpf` (legacy probe compiled
  from kernel headers, which **are** installed on the host).
* Second layer (the subtle one): the driver-loader initContainer failed compiling the
  probe with `member reference base type 'struct percpu_counter[4]'` at
  `fillers.h:875` — the **pre-6.2-kernel code path** compiled on a 6.8 kernel. The
  feature test that should set `HAS_RSS_STAT_ARRAY` compiles fine but then runs the
  host headers' **`modpost` binary inside the Debian-12 loader container**:
  `modpost: /lib/aarch64-linux-gnu/libc.so.6: version 'GLIBC_2.38' not found`. Ubuntu
  24.04-built kbuild tooling cannot execute on the loader's glibc 2.36 → the feature
  test *silently* fails → wrong branch → probe miscompiles.
  **Fix:** built the probe **on the host** (native modpost; `apt install clang llvm`,
  source tree extracted from the `falco-driver-loader:0.40.0` image, plain
  `make KERNELDIR=/lib/modules/$(uname -r)/build` → `HAS_RSS_STAT_ARRAY` set correctly,
  `probe.o` 6 MB). Installed at `/opt/keos/falco/falco-bpf.o`; patched the DaemonSet:
  removed the `falco-driver-loader` initContainer, swapped the `root-falco-fs` emptyDir
  for that hostPath. Falco engine opened the syscall source with our probe.
  **Validated live:** `Terminal shell in container` (2× via real `kubectl exec -it`)
  and `Read sensitive file untrusted` (`cat /etc/shadow`) alerts fired.
* Bonus bug found: `falco.falco.http_output.url` pointed at `http://pocteo:8000/…` —
  `pocteo` is the **frontend** Service on port **80**; the agent API is `api:8000`.
  Fixed in values. (Delivery still blocked by the token gap — see §1.8.)

#### d) `ollama` — the predicted JetPack↔CUDA↔ollama mismatch, in four acts
1. **Chart pin `ollama/ollama:0.4.7`** (Dec 2024): logs
   `unsupported L4T version` for R39 (its newest Jetson runner is `cuda_jetpack6`,
   gated on `/etc/nv_tegra_release` major = 35/36), `driver=0.0` — broken on this stack.
2. **`ollama/ollama:0.30.8` (latest stock):** server runs, GPU **enumerated** through the
   CDI-injected driver (`device=Orin cc=870`) but **skipped**:
   `compute capability not in compiled architectures` — stock arm64 images compile CUDA
   kernels for server-ARM (sm_75…sm_121) and **omit sm_87** (Orin iGPU) → silent CPU.
3. **`dustynv/ollama:r36.4-cu129-24.04` (jetson-containers):** two image bugs
   (CMD `/start_ollama` shipped without the execute bit; 0-byte driver-lib stubs
   shadowing the CDI-injected host driver), and after working around both, the r36
   binary **segfaults inside `cuInit`** against the r39 driver — real ABI mismatch,
   dead end.
4. **Winner:** official image + official Jetson runner overlay. ollama publishes
   `ollama-linux-arm64-jetpack6.tar.zst` per release (contains only
   `lib/ollama/cuda_jetpack6` — CUDA 12.6 kernels **with sm_87**). Built locally:
   ```dockerfile
   FROM ollama/ollama:0.30.7
   COPY jp6/lib/ollama/cuda_jetpack6 /usr/lib/ollama/cuda_jetpack6
   ```
   → `keos-ollama-jetson:0.30.7-jp6`, imported into k3s containerd. The CUDA 12.6
   runner works against the 13.2 host driver (minor-version compatibility).
   **Last gate:** ollama skips `cuda_jetpack*` dirs unless it recognises the L4T release
   — and the skip-log's suggestion `OLLAMA_LLM_LIBRARY` does **not** bypass that check
   (verified in `discover/runner.go`). The real override is **`JETSON_JETPACK=6`**
   (env). Added `ollama.env` passthrough to the chart for it.

**Result:** `inference compute: library=CUDA compute=8.7 name=CUDA0 description=Orin
driver=13.2 type=iGPU` → `ollama ps` **100% GPU**.

### 1.5 Node OOM — 8 GB + zero swap

During the (re)install's model pull, the kernel **OOM-killed `llama-server`**
(`global_oom`, 2.3 GB RSS — the agent had already triggered a chat-model load while the
embed model was still downloading, on top of ~2.5 GB of desktop apps). The exec'd
`ollama pull` died with it (exit 137) and took the installer down mid-postinstall.
**Fix:** persistent **6 GiB swapfile** (`/swapfile`, fstab) — k3s runs kubelet with
`--fail-swap-on=false`, so this is safe. Finished the skipped post-install steps by hand
(`ollama pull nomic-embed-text`, `kubectl apply manifests/namespaces.yaml +
demo-workloads.yaml`, `python -m agent.seed_rag` → 16 runbooks indexed).

### 1.6 GPU inference proof (criterion #2)

* `ollama ps`: qwen2.5:3b-instruct-q4_K_M → **100% GPU** (2.2 GB).
* Sustained generation **11.6 tok/s** (188 tokens), **20.6 tok/s** uncontended;
  prompt prefill **360 tok/s**; load 9 s cold / 0.5 s warm.
* Embedding model co-resident at 50/50 CPU/GPU under memory pressure (expected on 8 GB).

### 1.7 The security loop (criterion #3) — three agent-side bugs, then 34 seconds

Fired `keosctl run lib-reverse-shell` (after `keosctl mode active`). Three failures in
sequence, each a **first-run-on-ARM64** bug in our own stack:

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | graph crashed at `retrieve_context`: `OSError: Exec format error: 'kubectl'` | `agent/Dockerfile.api` + `mcp_server/Dockerfile` hardcode the **amd64** kubectl download | download `linux/$(dpkg --print-architecture)/kubectl`; rebuilt both images under the same release tag, re-imported, bounced pods |
| 2 | `keosctl run` → HTTP 404 "use case not found" | the starter use-case library is only seeded into `~/.shp-usecases.json` **inside `GET /api/usecases`** (`agent/api.py:2140`); a fresh api pod that receives `POST /run` first 404s | workaround: list before run. Proper fix in Part 2 (§2.6) |
| 3 | `isolate_pod failed: … error loading config file "/root/.kube/config": permission denied` while **OPA said allow** | `mcp_server/Dockerfile` bakes `ENV KUBECONFIG=/root/.kube/config` (compose/laptop leftover). In-cluster, the pod runs as uid 1000: kubectl **hard-fails** loading the unreadable path instead of falling back to the ServiceAccount. (`agent/config.py:kubectl_auth_args()` correctly skips `--kubeconfig` — but the *env var* reaches kubectl directly.) Also `HOME=/root` from the base image aims kubectl's default chain at an unreadable dir. | chart now sets `HOME=/tmp` on api + mcp-server and `KUBECONFIG=""` on mcp-server (empty = unset for kubectl → in-cluster auth engages) |

Hot-patching the running pods was impossible by design (`readOnlyRootFilesystem`,
tmpfs `/tmp`) — a nice validation of the hardening, and the reason fixes had to be
image/chart-level.

**Final run:** fired 17:55:39 → **17:56:13**: diagnosis 0.82 confidence (correct runbook
`security/001-reverse-shell` retrieved at 0.70), plan `isolate_pod`, OPA allow (active
mode), **real** `keos.pocteo/quarantine=true` label + deny-all NetworkPolicy
`keos-isolate-nginx-demo-…` in `workloads`, verify → healed. **≈34 s end-to-end** vs
~9 min on WSL CPU.

### 1.8 Remaining gaps (known, documented)

1. **Falco → agent delivery:** `/api/webhooks/falco` **fails closed** without
   `FALCO_WEBHOOK_TOKEN` (`agent/api.py:588`), the token is not in the auth secret, and
   the chart has no way to make Falco send an `Authorization` header. Detections fire
   (stdout/webhook attempt) but never become incidents. → §2.5.
2. **Tetragon:** impossible on this kernel until NVIDIA ships BTF or the kernel is
   rebuilt with `CONFIG_DEBUG_INFO_BTF=y`. Parked, clean.
3. **Falco probe is kernel-bound:** `/opt/keos/falco/falco-bpf.o` must be rebuilt after
   a kernel upgrade (build tree kept at `/tmp/falco-build`).

### 1.9 Uncommitted working-tree changes (to commit / upstream)

| File | Change |
|---|---|
| `agent/Dockerfile.api`, `mcp_server/Dockerfile` | kubectl arch from `dpkg --print-architecture` |
| `deploy/helm/pocteo/values.yaml` | ollama → `keos-ollama-jetson:0.30.7-jp6` + `JETSON_JETPACK=6`; opa → `0.69.0-static`; falco `driver.kind: ebpf` + webhook URL → `http://api:8000/...`; tetragon nodeSelector park |
| `deploy/helm/pocteo/templates/stateful-stores.yaml` | optional `ollama.command` / `ollama.env` passthrough |
| `deploy/helm/pocteo/templates/api.yaml`, `mcp-server.yaml` | `HOME=/tmp`; mcp also `KUBECONFIG=""` |

Host-level state (Jetson only, not in git): CDI spec + cdi mode, swapfile,
`/opt/keos/falco/falco-bpf.o`, `keos-ollama-jetson:0.30.7-jp6` in containerd, falco DS
patch (live object only — a `helm upgrade` will revert it; values changes keep falco on
`ebpf` but the probe-mount patch must be re-applied or productised per §2.5).

---

## Part 2 — Hardening plan: first-try installs on any distro / k3s / Kairos / Talos

Everything below is grounded in the current code (`scripts/install-edge.sh`,
`scripts/install.sh`, `scripts/keosctl`, `deploy/helm/pocteo`). Ordered P0 → P2.

### 2.1 P0 — Publish multi-arch first-party images (kills the whole §1.7 bug class)

Today the GHCR images are effectively amd64-only and private, so every ARM64 install is
forced through `--local-build` (slow, needs docker + repo clone + writable host — already
impossible on Talos/Kairos).

* CI (GitHub Actions) with `docker buildx build --platform linux/amd64,linux/arm64` for
  `keos-api`, `keos-mcp-server`, `keos-frontend`; push to `ghcr.io/pocteo-platform/*`.
* The two Dockerfile arch fixes from §1.7 are prerequisites (already in the tree).
  While at it: **delete `ENV KUBECONFIG=...` from `mcp_server/Dockerfile`** (the
  compose path can set it in `docker-compose.yml`) and set `ENV HOME=/tmp` in both
  runtime images — fix at the source, keep the chart overrides as belt-and-braces.
* Acceptance: fresh arm64 + amd64 cluster, no docker on the operator machine,
  `install-edge.sh` with public images completes with zero `--local-build`.

### 2.2 P0 — Third-party image multi-arch audit (chart values)

One-time sweep + CI guard (a script that `docker manifest inspect`s every pinned tag and
fails if `arm64` is missing):

| Image | Status | Action |
|---|---|---|
| `openpolicyagent/opa` | bare tags amd64-only | **done:** `-static` suffix |
| `ollama/ollama` | multi-arch but no sm_87 | Jetson variant — §2.4 |
| `qdrant`, `nats:alpine`, `caddy:alpine` | multi-arch ✓ | pin check in CI |
| falco / tetragon / chaos-mesh subcharts | multi-arch ✓ | capability-gated — §2.5 |
| `alpine/k8s:1.30.0` (node-label hook) | **verify** | swap to multi-arch kubectl image (e.g. `bitnami/kubectl` or `rancher/kubectl`) if not |

### 2.3 P1 — `keosctl preflight` (new subcommand) + wire into installer & doctor

The 10-minute `helm --wait` timeout is how every environment problem currently presents
itself. Catch them in seconds *before* helm. Checks, each with an actionable one-line
remedy:

1. **Binaries:** kubectl, helm, openssl, python3 (+ docker only if `--local-build`).
2. **Cluster reachability + distro probe** (existing logic, lines 199-275) — print the
   chosen containerd socket and let `--containerd-socket` override it.
3. **Node arch(es):** warn when first-party images lack the node arch (manifest inspect).
4. **Memory:** node allocatable vs chosen model (1.5b/3b → ≥8 GB total with ≥3 GB free;
   7b → 16 GB). On 8 GB: recommend swap (k3s/Ubuntu), auto-set
   `OLLAMA_MAX_LOADED_MODELS=1`, suggest `--skip-models` re-pull later. On **Talos**
   (no swap, immutable): hard-block the 7b choice.
5. **GPU (when `--gpu`/auto):** `nvidia.com/gpu` allocatable on the target node — the
   existing gate, but moved before helm with platform-specific remediation text (§2.4).
6. **eBPF capability for the security domain:** does any node expose
   `/sys/kernel/btf/vmlinux`? (probe via a 5-second `alpine` Job with a hostPath, or a
   node-label convention). Decide per §2.5 and *tell the operator what was decided and why*.
7. **WSL2 detection** (already exists, line 544) — keep.
8. Output `keosctl preflight --json` for CI.

### 2.4 P1 — GPU enablement per platform (especially Jetson)

The crux on Jetson was (a) CDI mode + the `tegra` alias, (b) the sm_87 image, (c)
`JETSON_JETPACK`. Productise all three:

* **Detect Tegra from the cluster**: node label `nvidia.com/gpu.machine`,
  `kubectl get node -o jsonpath='{.status.nodeInfo.kernelVersion}'` containing `-tegra`,
  or `feature.node.kubernetes.io/...` if NFD is present. No SSH needed.
* **Jetson ollama image as a first-party artifact:** CI job that, per supported ollama
  release, builds `ghcr.io/pocteo-platform/keos-ollama-jetson:<ver>-jp6` =
  `ollama/ollama:<ver>` + the official `jetpack6` tarball overlay (the §1.4d recipe —
  two lines of Dockerfile). When Tegra is detected, the installer sets
  `ollama.image.repository/tag` + `ollama.env: JETSON_JETPACK=6` automatically.
  Track upstream: when ollama learns R39 / ships a jetpack7 runner, drop the env.
* **Host CDI setup**: on plain-Linux/k3s the installer (or a documented
  `keosctl gpu-setup` helper that prints the 3 commands when it lacks root) applies:
  `nvidia-ctk cdi generate --mode=csv` + **tegra alias injection** + runtime `mode=cdi`.
  Upstream the alias bug to nvidia-container-toolkit (CSV mode should accept the device
  plugin's `tegra` id) and to k8s-device-plugin — until then we carry the workaround.
* **Talos:** no host access by design — GPU comes from the NVIDIA **system extensions**
  (`nonfree-kmod-nvidia` + `nvidia-container-toolkit`) declared in machine config; the
  installer's job is only to *verify* allocatable and point at the Talos doc when absent.
* **Kairos (Jetson):** bake toolkit + CDI spec (with the alias) into the Kairos image /
  cloud-config — immutable OS, so it must be in the image recipe, not post-install. Add
  this to `website/docs/platforms/kairos-jetson.md`.
* Keep `--no-gpu` as the universal fallback (works today).

### 2.5 P1 — Security domain: capability-gated, with prebuilt probes and a working webhook

**Driver selection ladder** (installer/preflight decides per cluster, records the choice
in values):

1. Kernel has BTF (`/sys/kernel/btf/vmlinux`) → `falco.driver.kind=modern_ebpf` **and**
   tetragon enabled (label the nodes `keos.pocteo/btf-kernel=true` — the values
   nodeSelector park from §1.4b then *just works* in reverse). Talos qualifies (its
   kernel ships BTF); most stock distro kernels ≥5.8 qualify; **L4T and WSL2 do not**.
2. No BTF but kernel headers on host → legacy `ebpf` — but **fix the loader**:
   the Debian-12 loader image cannot exec Ubuntu-24.04 kbuild tooling (the §1.4c glibc
   wall). Options, in preference order:
   a. **Prebuilt probe artifacts**: CI builds `falco-bpf.o` per supported kernel
      (`6.8.12-1021-tegra`, Kairos pinned kernels, …) into an OCI artifact; an
      initContainer fetches by `uname -r` into the probe volume — no on-node compilation
      ever. This is also the **only** option on immutable Kairos (no toolchain on host).
   b. Loader image matched to the node distro (Ubuntu 24.04-based loader) — fragile.
   c. Documented host-build path (what we did) for hands-on edge boxes.
3. Neither → `--no-security` semantics automatically: sensors off, synthetic-event path
   stays (the demo still works), **clear preflight message**.

**Webhook delivery (the §1.8 gap):**
* Installer generates `FALCO_WEBHOOK_TOKEN` next to the api token, adds it to the
  `keos-pocteo-auth` secret (api already reads it; fails-closed today).
* Falco side: the falco chart ≥4.x supports `falco.http_output` custom headers via
  `falco.yaml` only in recent versions — if the pinned 4.21.3 cannot send
  `Authorization`, deploy **falcosidekick** (subchart already available inside the falco
  chart; it supports per-output custom headers) targeting
  `http://api:8000/api/webhooks/falco`. Either way, add an e2e check to `keosctl doctor`:
  trigger `event-generator` once and assert an incident appears.
* The URL fix (`api:8000`, not `pocteo:8000`) is already in values — add a template-level
  derivation (`http://{{ include "pocteo.apiService" . }}:8000/...`) so it can't drift.

### 2.6 P1 — Agent/runtime correctness fixes found during validation

* **Use-case seeding at startup**, not first-list: move
  `usecase_store.seed_library_if_empty()` into the FastAPI lifespan (or call it from
  `run_usecase` too). Removes the `POST /run` → 404 ordering trap (§1.7-2).
* **`kubectl_auth_args()` should also neutralise the env**: when the configured
  kubeconfig is absent/unreadable, pass `--kubeconfig=/dev/null`-equivalent or scrub
  `KUBECONFIG` from the subprocess env so a poisoned env var can never bypass the
  helper's logic again.
* **Verify-step UX**: `proposed_tool` on the incident ends up as `page_human` even when
  the executed tool was `isolate_pod` (confusing in the API/SPA). Cosmetic, but worth a
  ticket.
* **`keosctl doctor` additions**: in-pod `kubectl version --client` (arch sanity),
  `ollama ps` processor column (GPU placement), BTF presence, falco→api delivery (above),
  swap presence on <16 GB nodes.

### 2.7 P2 — Installer robustness

* `helm upgrade --install` already; add `--atomic` *or* keep the failed-release handling
  we exercised (uninstall-then-install) as an explicit `--repair` path. A failed first
  release currently strands the operator (§1.3-3).
* Scope `--wait`: core components via helm `--wait`; security DaemonSets via a
  post-install watch loop with **per-component, actionable** errors ("falco probe build
  failed on kernel X — see <doc>") instead of one opaque 10-minute timeout.
* Post-helm steps (model pull, demo workloads, RAG seed) should be **resumable**:
  `install-edge.sh --resume-postinstall` (today an OOM mid-pull kills the script and the
  remaining steps silently never happen — §1.5).
* Model pull resilience: retry on exit 137 with a hint about memory; pull embed model
  first (small), then chat.

### 2.8 P2 — Platform matrix to encode in the distro probe (and document)

| | detection (exists) | containerd socket | GPU path | swap | security sensors | local-build possible |
|---|---|---|---|---|---|---|
| **k3s** (incl. Jetson/Ubuntu) | version string | `/run/k3s/containerd/containerd.sock` ✓ | auto-runtime + CDI (§2.4) | yes — recommend on 8 GB | BTF? → ladder §2.5 | yes (docker on host) |
| **Kairos** (k3s bundled) | osImage label ✓ | `/run/k3s/containerd/containerd.sock` ✓ | toolkit+CDI baked in image (§2.4) | via cloud-config only | **prebuilt probes only** (immutable) | **no** → public/airgap images required |
| **Talos** | version/labels ✓ | `/run/containerd/containerd.sock` ✓ | NVIDIA system extensions; verify-only | **no swap** → gate models by RAM | BTF ✓ → modern_ebpf + tetragon OK | **no** → public/airgap images required |
| **RKE2** | version string ✓ | `/run/k3s/containerd/containerd.sock` ✓ | standard toolkit flow | yes | ladder | yes |
| **kubeadm / generic Debian-Ubuntu** | fallback ✓ | `/run/containerd/containerd.sock` ✓ | standard toolkit flow | yes | ladder | yes |
| **WSL2 (dev)** | `/proc/version` ✓ | k3d/kind | none | n/a | force-off (exists) | yes |

Key consequence the matrix makes explicit: **Talos and Kairos can never `--local-build`**
— P0 multi-arch public images (§2.1) are the hard prerequisite for those platforms, and
the installer should say so instead of dying on a missing docker.

### 2.9 P2 — CI / release pipeline

1. **Build:** buildx multi-arch for the 3 first-party images + the Jetson ollama variant
   + (new) falco probe artifacts per supported kernel. Publish chart to OCI on tag.
2. **Static guards:** Dockerfile lint forbidding hardcoded `amd64|x86_64` in URLs;
   manifest-inspect check for every pinned third-party tag (§2.2).
3. **e2e matrix:** kind (amd64) and a self-hosted Jetson runner (arm64+GPU) running:
   preflight → install → `keosctl doctor` → `lib-reverse-shell` → assert quarantine
   label + netpol + incident < 90 s. Talos e2e via `talosctl cluster create` (amd64) for
   the socket/no-docker/no-swap paths.

### 2.10 Suggested phasing

| Phase | Items | Exit criterion |
|---|---|---|
| **P0** | §2.1 multi-arch images (incl. Dockerfile env hygiene), §2.2 image audit, commit Part-1 fixes | arm64 k3s install with **no** `--local-build`, zero crash-loops |
| **P1** | §2.3 preflight, §2.4 GPU automation, §2.5 sensors ladder + webhook token, §2.6 agent fixes | fresh Jetson: single command → all 4 validation criteria green, falco events become incidents |
| **P2** | §2.7 installer robustness, §2.8 matrix encoding, §2.9 CI/e2e | Talos + Kairos e2e green in CI; failed installs always end with an actionable message |

---

### Appendix — verified numbers for this device (for regression comparison)

* `nvidia.com/gpu` allocatable: 1 · device plugin v0.16.2 unpatched
* qwen2.5:3b q4_K_M: 100% GPU, 11.6 tok/s sustained / 20.6 uncontended, 360 tok/s prefill
* `lib-reverse-shell`: fire → verified-healed in **34 s** (graph: 2 LLM calls + verify)
* Falco 0.40 + host-built legacy probe: `Terminal shell in container`,
  `Read sensitive file untrusted` both firing live
* Stack: 15/15 pods Running (tetragon parked by design)
