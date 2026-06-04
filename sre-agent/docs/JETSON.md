# Running Keos on NVIDIA Jetson

Keos was designed with edge K8s in mind. On a **Jetson Orin Nano (8 GB)**
running an **immutable Kubernetes OS — Talos or Kairos** — the same
Helm chart that powers the laptop demo runs ~10× faster end-to-end by
enabling the GPU.

> **Want the day-1 walkthrough instead?** See
> [`JETSON_FIRST_BOOT.md`](JETSON_FIRST_BOOT.md) — that's the operator's
> "I just received the box" guide. The doc you're reading now is the
> **platform reference**: every knob, sizing, GPU plumbing, fallback to
> legacy k3s.

Expected behaviour, side by side:

| Path | CPU laptop | Jetson Orin Nano (GPU) |
|---|---|---|
| Full graph run (Observe → Heal) | **4–5 min** | **20–30 s** |
| `diagnose` LLM call | 60–90 s | 5–10 s |
| `plan_action` LLM call | 30–60 s | 3–5 s |
| Embedding (nomic-embed-text) | 100–300 ms | <50 ms |
| Cold model load (first call) | 60–90 s | 15–25 s |

The agent's safety mesh (OPA, AgentMode CRD, MCP boundary, HMAC audit) is
**unchanged** — same code, same tests. Only inference speed shifts.

---

## Hardware

**Recommended:** Jetson Orin Nano 8 GB (NVIDIA Jetson Orin Nano Developer
Kit, SKU 945-13766-0005-000). The 4 GB variant works but you'll need to
use a smaller model (see "Resource sizing" below).

| Resource | Footprint |
|---|---|
| Disk | ~20 GB (Ollama models + Qdrant + NATS + container images) |
| RAM | 6 GB peak under load (4 GB Qwen2.5-3B + 2 GB everything else) |
| Power mode | `nvpmodel -m 0` (15 W MAXN) gives best throughput; `-m 1` (7 W) trades ~20% throughput for half the power draw |

Storage: an NVMe SSD via the M.2 Key M slot is strongly recommended over
the bundled microSD. Cold model load goes from ~25 s on SSD to ~90 s on
microSD.

---

## Supported OSes — pick one path

| OS | When to choose | Day-1 setup |
|---|---|---|
| **Talos** | Want a public, mature, well-documented immutable OS. Lots of community + commercial support. | [Step 1A](#step-1a--talos) |
| **Kairos** | Immutable Linux for edge K8s ([kairos.io](https://kairos.io)). Bundles k3s by default (k0s/RKE2 are options). SSH-friendly via cloud-init. | [Step 1B](#step-1b--kairos) |
| **k3s (legacy)** | You inherited a Jetson that already runs k3s, or you can't use an immutable OS for compliance reasons. | [Step 1C](#step-1c--k3s-legacy-shape) |

The Helm chart + installer adapt automatically based on what the
cluster reports — you don't pick the OS in `values.yaml`, you just
point `KUBECONFIG` at the cluster and run `scripts/install-edge.sh`.

---

## Step 1A — Talos

The OS image is built by your platform team (or Sidero's image
factory) with:
- The Jetson's BSP layer (kernel + NVIDIA drivers + L4T).
- The `siderolabs/nvidia-container-toolkit` System Extension.
- A static `MachineConfig` declaring a single-node control-plane.
- (Optional) Pre-baked Keos container images so the install is
  air-gappable.

On the workstation:

```bash
# Fetch the kubeconfig from the Jetson's Talos API. Use the
# talosconfig your platform team gave you.
talosctl --talosconfig ./talosconfig.yaml \
         --nodes <jetson-ip>              \
         kubeconfig ~/.kube/keos-jetson.yaml

export KUBECONFIG=~/.kube/keos-jetson.yaml
kubectl get nodes              # → 1 Ready node
kubectl get runtimeclass       # → nvidia
kubectl describe node | grep nvidia.com/gpu
#   nvidia.com/gpu: 1   (Allocatable)
```

If all three commands look right, jump to [Step 4](#step-4--install-keos).

**Containerd socket path on Talos** is `/run/containerd/containerd.sock`
(NOT the k3s path). The installer auto-detects + overrides Chaos
Mesh's chaos-daemon socket path accordingly.

---

## Step 1B — Kairos

Kairos bundles **k3s by default** (k0s and RKE2 are alternative
options at OS-build time). SSH access is provisioned via cloud-init at
first boot — your OS-image builder seeded the operator's public key.
Fetch the kubeconfig via SSH from the standard k3s path:

```bash
# From the workstation. Default Kairos username is `kairos`.
ssh kairos@<jetson-ip> 'sudo cat /etc/rancher/k3s/k3s.yaml' \
  | sed "s/127.0.0.1/<jetson-ip>/" \
  > ~/.kube/keos-jetson.yaml

export KUBECONFIG=~/.kube/keos-jetson.yaml
kubectl get nodes              # → 1 Ready node, v1.30.4+k3s1
kubectl get runtimeclass       # → nvidia
```

If your Kairos image bundles k0s or RKE2 instead, the kubeconfig path
shifts (`/var/lib/k0s/pki/admin.conf`, `/etc/rancher/rke2/rke2.yaml`).

**Containerd socket path on Kairos (k3s default)** is
`/run/k3s/containerd/containerd.sock` — the same as standalone k3s,
**not** the Talos path. The installer detects this and applies the
correct Chaos Mesh override automatically.

Jump to [Step 4](#step-4--install-keos).

---

## Step 1C — k3s (legacy shape)

> **You only need this if you can't use Talos or Kairos.** It's the
> path Keos used to demo on; we keep the steps documented for backward
> compatibility but the immutable-OS paths above are the recommended
> targets.

### Step 1C.1 — Flash JetPack 6.x

Use NVIDIA SDK Manager on a host workstation:

```
JetPack:        6.0 GA or later
L4T:            36.3+
Components:    Linux for Tegra, NVIDIA Container Runtime, CUDA 12.2+
```

After flash, on the Jetson:

```bash
dpkg -l | grep -E "nvidia-container-toolkit|nvidia-l4t-jetson"
ls -l /dev/nvgpu /dev/nvhost-ctrl 2>/dev/null
```

### Step 1C.2 — Install k3s with the NVIDIA runtime registered

```bash
curl -sfL https://get.k3s.io | sh -s - \
    --write-kubeconfig-mode 644 \
    --disable traefik

sudo systemctl status k3s
kubectl get nodes
```

Add the `nvidia` runtime to k3s's containerd config:

```bash
sudo mkdir -p /var/lib/rancher/k3s/agent/etc/containerd
sudo tee /var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl > /dev/null <<'EOF'
{{ template "base" . }}

[plugins."io.containerd.grpc.v1.cri".containerd.runtimes."nvidia"]
  privileged_without_host_devices = false
  runtime_engine = ""
  runtime_root = ""
  runtime_type = "io.containerd.runc.v2"
  [plugins."io.containerd.grpc.v1.cri".containerd.runtimes."nvidia".options]
    BinaryName = "/usr/bin/nvidia-container-runtime"
EOF

sudo systemctl restart k3s
```

Register the `RuntimeClass`:

```bash
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: nvidia
handler: nvidia
EOF

kubectl get runtimeclass        # → nvidia
```

### Step 1C.3 — Install the NVIDIA device plugin

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml
kubectl -n kube-system get ds nvidia-device-plugin-daemonset
kubectl describe node | grep -E "nvidia.com/gpu|Allocatable" -A 5
# Expect: nvidia.com/gpu: 1 under Allocatable.
```

**Containerd socket path on standalone k3s** is
`/run/k3s/containerd/containerd.sock` (same as Kairos-on-k3s, differs
from Talos). The installer auto-detects + applies the right override.

---

## Step 4 — Install Keos

Run the workstation installer (driven by `scripts/install-edge.sh`):

```bash
# From the repo (or the unpacked tarball):
bash sre-agent/scripts/install-edge.sh

# OR, from the published distribution:
curl -fsSL https://get.keos.pocteo.platform/install-edge.sh | bash
```

What the installer does, in order:

1. Probes the cluster — distro, GPU capacity, node names.
2. Generates `apiToken` + `hmacKey` (cached at `~/.keos-token`).
3. Runs `helm upgrade --install` with sensible edge defaults:
   - `agent.attachment.mode=hard` + `bootstrapNodeLabel.enabled=true`
     pin the agent to the Jetson via a label, surviving node renames.
   - `chaos-mesh.chaosDaemon.socketPath` set to the right value for
     the detected distro (Talos = `/run/containerd/...`,
     Kairos/k3s = `/run/k3s/containerd/...`).
   - `ollama.gpu.enabled=true` + `runtimeClassName=nvidia` if GPU
     capacity was detected.
4. Waits up to 10 min for everything Ready.
5. Pulls the LLM models via `kubectl exec` against the Ollama pod.
6. Seeds the RAG corpus via `kubectl exec` against the api pod.

If you'd rather drive Helm by hand:

```bash
helm install keos deploy/helm/pocteo \
    --namespace keos --create-namespace \
    --set-string auth.apiToken="$(openssl rand -base64 32)" \
    --set-string auth.hmacKey="$(openssl rand -base64 32)" \
    --set agent.attachment.mode=hard \
    --set agent.attachment.nodeName=<jetson-node-name> \
    --set agent.bootstrapNodeLabel.enabled=true \
    --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock \
    --set ollama.gpu.enabled=true \
    --set ollama.gpu.runtimeClassName=nvidia \
    --set ollama.gpu.count=1 \
    --set ollama.resources.requests.memory=2Gi \
    --set ollama.resources.limits.memory=5Gi \
    --wait --timeout 10m
```

> On **Kairos (default k3s)** or **legacy k3s** the `socketPath`
> flag becomes `/run/k3s/containerd/containerd.sock`. The installer
> figures it out; if you're hand-driving Helm, you handle it.

Why those memory tweaks: the Orin Nano shares system + GPU RAM. We cap
Ollama at 5 GiB so we leave headroom for Qdrant + NATS + the OS.

Verify the GPU is actually being used:

```bash
kubectl -n keos exec sts/ollama -- nvidia-smi
# Should show the model loaded in GPU memory.
```

---

## Step 5 — Pull models + seed RAG

The installer does this automatically. If you ran Helm by hand:

```bash
kubectl -n keos exec deploy/api -- python -m agent.pull_models
kubectl -n keos exec deploy/api -- python -m agent.seed_rag
kubectl -n keos exec deploy/api -- python -m scripts.doctor
```

Pulling Qwen2.5-3B from HF Hub takes ~5 min on a residential connection.
Once cached on the NVMe, subsequent installs reuse it.

---

## Run a scenario

Open the SPA via the URL the installer printed (or
`kubectl port-forward`), paste the token, flip mode to `active`,
click Run on `lib-memory-leak`. Watch the pipeline complete in ~25 s
instead of 5 min.

---

## Power management

| Mode | Power budget | Throughput vs MAXN | When to use |
|---|---|---|---|
| `nvpmodel -m 0` (MAXN) | 15 W | 100% | Always, unless thermals say otherwise |
| `nvpmodel -m 1` (7W) | 7 W | ~55% | Battery-backed deployments, fanless enclosures |
| `nvpmodel -m 2` (15W Eff) | 15 W (efficient curve) | ~85% | Sustained 24/7 — slightly better thermals |

On **Talos** these knobs are set declaratively at OS-build time
(System Extension or machineconfig), not from a shell. On **Kairos**
they can be set either at OS-build time (cloud-init) or at runtime
via SSH. On legacy k3s: `sudo nvpmodel -m <id> && sudo jetson_clocks`.

The agent adapts to whatever power mode the OS gives it; no
Keos-side config needed.

---

## Resource sizing — smaller Jetsons

The 4 GB Orin Nano (or older Xavier NX 8 GB) can run Keos but needs a
smaller chat model:

```bash
helm upgrade keos deploy/helm/pocteo --reuse-values \
    --set ollama.chatModel=qwen2.5:1.5b-instruct-q4_K_M \
    --set ollama.resources.limits.memory=3Gi
```

`qwen2.5:1.5b` is ~900 MB at q4_K_M and runs comfortably on 4 GB Orin.
Diagnostic quality is noticeably lower — small models will pick the
wrong tool more often. The OPA gate still catches the high-risk cases;
the user-visible cost is more escalations.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| `kubectl describe pod ollama-0` shows `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` | Device plugin not running (Talos: System Extension not loaded; k3s: re-run Step 1C.3) |
| Ollama logs `cuda library not found` | Container can't see the GPU. The `runtimeClassName: nvidia` line isn't being honored — check `kubectl get sts/ollama -o yaml \| grep runtime`. |
| `tegrastats` shows GPU at 0% during graph runs (legacy k3s) | Ollama might have fallen back to CPU. Check `kubectl -n keos exec sts/ollama -- ollama ps` — the entry should show `100% GPU` in the PROCESSOR column. |
| Chaos Mesh chaos-daemon CrashLoops | Wrong containerd socket path. Re-run installer (auto-detects) OR `helm upgrade ... --set chaos-mesh.chaosDaemon.socketPath=<correct-path>` |
| `nvpmodel: command not found` | You're on Talos — there's no shell on the node. Power mode is set at OS-build time. On Kairos, SSH in and check whether JetPack tooling was bundled. |
| Pod stays `Pending` with `Insufficient memory` | The Orin Nano shares RAM; drop `ollama.resources.limits.memory` to 4Gi or switch to the 1.5b model. |

---

## What the agent uses GPU for (and doesn't)

| Workload | GPU? |
|---|---|
| `diagnose` LLM call | ✅ Yes — single biggest win |
| `plan_action` LLM call | ✅ Yes |
| Embeddings (RAG retrieval) | ✅ Yes (CUDA path in Ollama) |
| OPA evaluation | ❌ CPU — sub-millisecond regardless |
| kubectl / MCP calls | ❌ Network-bound |
| K8s event watching | ❌ Light I/O |
| Audit HMAC | ❌ CPU — sub-millisecond |

Only the LLM + embedding paths benefit. Everything else is fast enough on
CPU that GPU offload would add latency.

---

## See also

- [`JETSON_FIRST_BOOT.md`](JETSON_FIRST_BOOT.md) — operator's day-1 walkthrough
- [`TALOS.md`](TALOS.md) — Talos-specific notes (runtime socket, machineconfig)
- [`INSTALL.md`](INSTALL.md) — install matrix
- [`CONFIG_REFERENCE.md`](CONFIG_REFERENCE.md) — `ollama.gpu.*` Helm keys
- [`USER_GUIDE.md`](USER_GUIDE.md) — day-2 ops + perf tuning
- Upstream: <https://www.talos.dev/v1.7/talos-guides/install/boot-assets/> (Talos image factory)
- Upstream: <https://github.com/NVIDIA/k8s-device-plugin>
