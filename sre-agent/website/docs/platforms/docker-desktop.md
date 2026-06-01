---
id: docker-desktop
slug: /platforms/docker-desktop
title: Docker Desktop (laptop demo)
sidebar_position: 1
---

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

## 3. k3s on Jetson / Raspberry Pi

Same Helm command. The Keos images are published multi-arch
(`linux/amd64,linux/arm64`), so the Jetson's containerd pulls the arm64
variant automatically. Set `ollama.resources.limits` and the model
choice based on the device's RAM:

```yaml
ollama:
  resources:
    limits: { cpu: "4", memory: 6Gi }
  chatModel: qwen2.5:3b-instruct-q4_K_M  # default; ~2 GB
```

### Jetson with GPU acceleration

For the full edge target (Jetson Orin Nano running k3s), enable GPU
offload — drops a full graph run from ~5 min CPU to ~25 s GPU:

```bash
helm install pocteo deploy/helm/pocteo \
    --set ollama.gpu.enabled=true \
    --set ollama.gpu.runtimeClassName=nvidia \
    ...
```

**Complete walkthrough**, including the NVIDIA Container Toolkit + device
plugin install, RuntimeClass registration, power-management modes, and
troubleshooting: see [JETSON.md](/docs/platforms/jetson).

## 4. Talos

See [TALOS.md](/docs/platforms/talos) for the runtime/socket overrides:

```bash
helm install pocteo deploy/helm/pocteo \
    --namespace pocteo --create-namespace \
    --set chaos-mesh.chaosDaemon.runtime=containerd \
    --set chaos-mesh.chaosDaemon.socketPath=/run/containerd/containerd.sock \
    --set-string auth.apiToken=$(openssl rand -base64 32) \
    --set-string auth.hmacKey=$(openssl rand -base64 32)
```

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
