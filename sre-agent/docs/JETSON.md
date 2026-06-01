# Running Keos on NVIDIA Jetson

Keos was designed with edge K8s in mind. On a **Jetson Orin Nano (8 GB)**
running k3s, the same Helm chart that powers the laptop demo runs
~10× faster end-to-end by enabling the GPU.

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

## Step 1 — Flash JetPack 6.x

Use NVIDIA SDK Manager on a host workstation:

```
JetPack:        6.0 GA or later
L4T:            36.3+
Components:    Linux for Tegra, NVIDIA Container Runtime, CUDA 12.2+
```

After flash, on the Jetson:

```bash
# Verify the runtime is present + the device shows up.
dpkg -l | grep -E "nvidia-container-toolkit|nvidia-l4t-jetson"
ls -l /dev/nvgpu /dev/nvhost-ctrl 2>/dev/null
```

---

## Step 2 — Install k3s with the NVIDIA runtime registered

k3s ships its own embedded containerd. We need to tell it about the
`nvidia` runtime.

```bash
# Install k3s server. --write-kubeconfig-mode 644 so we can read it as
# the default user.
curl -sfL https://get.k3s.io | sh -s - \
    --write-kubeconfig-mode 644 \
    --disable traefik

# Wait for the node to be Ready.
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

Register the K8s `RuntimeClass`:

```bash
kubectl apply -f - <<EOF
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: nvidia
handler: nvidia
EOF
```

Verify:

```bash
kubectl get runtimeclass
# Expect: nvidia    nvidia    <age>
```

---

## Step 3 — Install the NVIDIA device plugin

The device plugin advertises `nvidia.com/gpu` as a schedulable resource
to the kubelet:

```bash
kubectl apply -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml

# Wait for the DaemonSet.
kubectl -n kube-system get ds nvidia-device-plugin-daemonset

# Confirm the GPU shows up as an allocatable resource.
kubectl describe node | grep -E "nvidia.com/gpu|Allocatable" -A 5
# Expect a line like `nvidia.com/gpu: 1` under Allocatable.
```

If `nvidia.com/gpu: 0` shows up instead, the device plugin can't see the
GPU. Check `kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset`.
Most common cause: the device plugin pod is using the wrong containerd
runtime — re-check the RuntimeClass + the device plugin's
`runtimeClassName` field.

---

## Step 4 — Install Keos with GPU enabled

```bash
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set-string auth.apiToken="$(openssl rand -base64 32)" \
    --set-string auth.hmacKey="$(openssl rand -base64 32)" \
    --set ollama.gpu.enabled=true \
    --set ollama.gpu.runtimeClassName=nvidia \
    --set ollama.gpu.count=1 \
    --set ollama.resources.requests.memory=2Gi \
    --set ollama.resources.limits.memory=5Gi \
    --wait --timeout 10m
```

Why those memory tweaks: the Orin Nano shares system + GPU RAM. We cap
Ollama at 5 GiB so we leave headroom for Qdrant + NATS + the OS.

Verify GPU is actually being used:

```bash
# Inside the Ollama pod:
kubectl -n pocteo exec sts/ollama -- nvidia-smi
# Should show the model loaded in GPU memory.

# Or watch live during a graph run:
sudo tegrastats   # GPU util should jump to 80-100% during diagnose
```

---

## Step 5 — Pull models + seed RAG

```bash
kubectl -n pocteo exec deploy/api -- python -m agent.pull_models
kubectl -n pocteo exec deploy/api -- python -m agent.seed_rag
kubectl -n pocteo exec deploy/api -- python -m scripts.doctor
```

Pulling Qwen2.5-3B from HF Hub takes ~5 min on a residential connection.
Once cached on the NVMe, subsequent installs reuse it.

---

## Run a scenario

Open the SPA via the Ingress (or `kubectl port-forward`), paste the
token, flip mode to `active`, click Run on `lib-memory-leak`. Watch the
pipeline complete in ~25 seconds instead of 5 minutes.

---

## Power management

| Mode | Power budget | Throughput vs MAXN | When to use |
|---|---|---|---|
| `nvpmodel -m 0` (MAXN) | 15 W | 100% | Always, unless thermals say otherwise |
| `nvpmodel -m 1` (7W) | 7 W | ~55% | Battery-backed deployments, fanless enclosures |
| `nvpmodel -m 2` (15W Eff) | 15 W (efficient curve) | ~85% | Sustained 24/7 — slightly better thermals |

Switch with `sudo nvpmodel -m <id> && sudo jetson_clocks`. The agent
adapts to whatever it gets; no Keos-side config needed.

---

## Resource sizing — smaller Jetsons

The 4 GB Orin Nano (or older Xavier NX 8 GB) can run Keos but needs a
smaller chat model:

```bash
helm upgrade pocteo deploy/helm/pocteo --reuse-values \
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
| `kubectl describe pod ollama-0` shows `0/1 nodes are available: 1 Insufficient nvidia.com/gpu` | Device plugin not running, or `RuntimeClass` not applied. Re-run Step 3. |
| Ollama logs `cuda library not found` | Container can't see the GPU. The `runtimeClassName: nvidia` line isn't being honored — check `kubectl get sts/ollama -o yaml \| grep runtime`. |
| `tegrastats` shows GPU at 0% during graph runs | Ollama might have fallen back to CPU. Check `kubectl -n pocteo exec sts/ollama -- ollama ps` — the entry should show `100% GPU` in the PROCESSOR column. |
| `nvpmodel: command not found` | Not running on Tegra. Re-flash JetPack. |
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

- [INSTALL.md](INSTALL.md) — install matrix entry pointing here
- [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) — `ollama.gpu.*` Helm keys documented
- [USER_GUIDE.md §9](USER_GUIDE.md) — day-2 ops, includes a perf-tuning section
- Upstream: <https://docs.nvidia.com/datacenter/cloud-native/kubernetes/install-k8s.html>
- Upstream: <https://github.com/NVIDIA/k8s-device-plugin>
