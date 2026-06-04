# Keos SRE Agent — Installation guide

Pick the install path that matches your target. The Helm chart at
`deploy/helm/pocteo/` is the canonical install; `make demo-ui` is the
laptop-iteration shortcut.

## Prerequisites

| Tool | Why |
|---|---|
| Docker (for the laptop path) | Container runtime |
| `helm` ≥ 3.13 | Chart install |
| `kubectl` ≥ 1.27 | All targets |
| `openssl` | Generate auth + HMAC secrets |
| Node 20+ (only if building the SPA locally) | Frontend rebuild |
| Python 3.12 + `uv` (only for host-side dev) | Iteration |

## 1. Laptop demo (Docker Desktop)

```powershell
.\make.ps1 install         # one-time: pulls Ollama models (~2 GB)
.\make.ps1 demo-ui         # builds + starts everything, opens the SPA
```

Open `http://localhost`. Paste the bearer token from `.env.local` on
first launch. The full stack runs as docker-compose services + a k3d
cluster.

## 2. Generic K8s cluster

```bash
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set-string auth.apiToken=$(openssl rand -base64 32) \
    --set-string auth.hmacKey=$(openssl rand -base64 32) \
    --wait --timeout 5m
```

Get the token + open the SPA per the Helm `NOTES.txt` printout.

## 3. Edge — Talos or Kairos on Jetson / Raspberry Pi (recommended)

The supported edge target is an **immutable Kubernetes OS** on the
device: either **Talos** ([talos.dev](https://www.talos.dev)) or
**Kairos** ([kairos.io](https://kairos.io)). Both ship preconfigured
with containerd, the NVIDIA Container Toolkit (on Jetson hardware),
and the `nvidia` RuntimeClass. Both let you install Keos from your
workstation — Talos has no shell on the node (managed via `talosctl`),
Kairos exposes SSH via cloud-init for occasional debugging.

```bash
# Fetch the kubeconfig from the Jetson:
#   Talos:  talosctl --talosconfig ./talosconfig --nodes <ip> kubeconfig ~/.kube/keos-jetson.yaml
#   Kairos: ssh kairos@<ip> 'sudo cat /etc/rancher/k3s/k3s.yaml' \
#                | sed "s/127.0.0.1/<ip>/" > ~/.kube/keos-jetson.yaml
export KUBECONFIG=~/.kube/keos-jetson.yaml

# Drive the install (auto-detects Talos vs Kairos vs k3s):
bash sre-agent/scripts/install-edge.sh
```

The installer picks the right `chaos-mesh.chaosDaemon.socketPath`
based on the detected distro:

| Distro | containerd socket | Bundled K8s |
|---|---|---|
| Talos | `/run/containerd/containerd.sock` | k8s direct |
| Kairos (default) | `/run/k3s/containerd/containerd.sock` | k3s |
| k3s (standalone) | `/run/k3s/containerd/containerd.sock` | k3s |

The Keos images are published multi-arch (`linux/amd64,linux/arm64`)
so the Jetson's containerd pulls the arm64 variant automatically. The
installer enables GPU offload (`ollama.gpu.enabled=true`,
`runtimeClassName=nvidia`) when it sees `nvidia.com/gpu` capacity on
any node — drops a full graph run from ~5 min CPU to ~25 s GPU.

**Day-1 walkthrough** (what you'll see + type in order): see
[`JETSON_FIRST_BOOT.md`](JETSON_FIRST_BOOT.md).

**Platform reference** (sizing, knobs, power modes, GPU plumbing,
legacy k3s fallback): see [`JETSON.md`](JETSON.md).

**Talos specifics** (machine config, storage class, image cache): see
[`TALOS.md`](TALOS.md).

## 4. k3s on Jetson / Raspberry Pi (legacy)

Only use this if you can't use Talos or Kairos (compliance, existing
fleet, etc.). The Helm chart still works:

```bash
helm install keos deploy/helm/pocteo \
    --namespace keos --create-namespace \
    --set chaos-mesh.chaosDaemon.socketPath=/run/k3s/containerd/containerd.sock \
    --set ollama.gpu.enabled=true \
    --set ollama.gpu.runtimeClassName=nvidia \
    --set-string auth.apiToken=$(openssl rand -base64 32) \
    --set-string auth.hmacKey=$(openssl rand -base64 32) \
    --wait --timeout 10m
```

Full k3s + NVIDIA toolkit walkthrough: [`JETSON.md` §1C](JETSON.md#step-1c--k3s-legacy-shape).

## 5. Air-gapped

Pre-pull the images into the cluster nodes' image cache so Helm install
doesn't need a registry connection.

For Talos:

```bash
talosctl image cache \
    ghcr.io/your-org/pocteo-api:0.1.0 \
    ghcr.io/your-org/pocteo-mcp-server:0.1.0 \
    ghcr.io/your-org/pocteo-frontend:0.1.0 \
    ollama/ollama:0.4.7 \
    qdrant/qdrant:v1.12.4 \
    nats:2.10.22-alpine \
    openpolicyagent/opa:0.69.0 \
    caddy:2.8-alpine
```

For k3s nodes:

```bash
k3s ctr images pull ghcr.io/your-org/pocteo-api:0.1.0
# ... etc.
```

Then install with `--set global.imageRegistry=<your-mirror>` so the chart
references the local registry instead of the default.

## Verifying the install

```bash
kubectl -n pocteo get pods            # all should be Running within ~2 min
kubectl -n pocteo logs deploy/api -f  # follow agent startup
kubectl -n pocteo port-forward svc/pocteo 8080:80
# Open http://localhost:8080
# Paste the token from:
kubectl -n pocteo get secret pocteo-pocteo-auth \
    -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d
```

## Uninstall

```bash
helm uninstall pocteo -n pocteo
kubectl delete ns pocteo
```

PVCs persist by default — delete the namespace to drop them, or migrate
data to a backup volume first.
