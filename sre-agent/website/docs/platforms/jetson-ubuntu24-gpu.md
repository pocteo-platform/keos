---
id: jetson-ubuntu24-gpu
slug: /platforms/jetson-ubuntu24-gpu
title: Jetson Orin + Ubuntu 24 — GPU ollama inference
sidebar_position: 6
description: A copy-paste runbook for getting Keos's ollama running on the Jetson Orin GPU on stock Ubuntu 24, and verifying the model is actually offloaded to the GPU (tok/s, layer offload, tegrastats).
---

# Jetson Orin + Ubuntu 24 — GPU ollama inference

The goal of this guide is narrow and specific: **get the Keos agent's ollama
model running on the Jetson Orin GPU on stock Ubuntu 24, and *prove* the
inference is on the GPU** (not silently falling back to CPU). Everything else
(k3s, the Keos stack) is in service of that.

> **The single biggest risk** is a JetPack ↔ CUDA ↔ ollama version mismatch:
> ollama loads the model but inference fails or runs on CPU. This guide tells
> you how to detect that and what to do. (We hit the discrete-GPU cousin of
> this bug during development — a stale model blob made ollama return HTTP 500
> on every generate even though the model "loaded".)

## 0 · Prerequisites

| | |
|---|---|
| Board | Orin **NX 16 GB** comfortable · Orin **Nano 8 GB** is tight (use the 3B model, consider `--skip-models` for other stores) |
| OS | Ubuntu 24.04 on the Jetson = **JetPack 6.1+** territory. Confirm: `cat /etc/nv_tegra_release` |
| Driver/CUDA | `nvidia-smi` (or on older L4T, `tegrastats`) must work on the host first |
| Network | The private Keos images aren't anonymously pullable → you'll use `--local-build` (builds ARM64 natively on the board) |

```bash
# sanity: GPU visible on the host?
nvidia-smi || sudo tegrastats --interval 1000   # Ctrl-C
cat /etc/nv_tegra_release
free -h        # confirm RAM headroom
```

## 1 · k3s (the cluster keosctl installs *into*)

`keosctl install` deploys Keos into an **existing** cluster — it doesn't
create one. Install k3s first:

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes        # Ready?
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER" ~/.kube/config
export KUBECONFIG=~/.kube/config
kubectl get nodes                 # Ready
```

Ubuntu 24's cgroup v2 is handled by k3s — no extra flags needed.

## 2 · Wire the NVIDIA runtime into k3s's containerd (the GPU enablement)

This is the step keosctl assumes is already done. Without it, the GPU is
invisible to pods.

```bash
# NVIDIA Container Toolkit (JetPack usually ships nvidia-container-runtime;
# install/upgrade if missing)
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit

# Register the 'nvidia' runtime in k3s's OWN containerd config.
# k3s uses a templated config — add nvidia as a runtime:
sudo nvidia-ctk runtime configure --runtime=containerd \
  --config=/var/lib/rancher/k3s/agent/etc/containerd/config.toml.tmpl 2>/dev/null \
  || echo "if config.toml.tmpl doesn't exist, create it from the live config.toml and add the nvidia runtime block"

sudo systemctl restart k3s
```

Create the RuntimeClass + the NVIDIA device plugin so pods can request
`nvidia.com/gpu`:

```bash
kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: nvidia
handler: nvidia
EOF

# NVIDIA device plugin (use the Jetson/L4T-aware build if the generic one
# doesn't register the GPU):
kubectl create -f https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/v0.16.2/deployments/static/nvidia-device-plugin.yml
```

**Verify the GPU is schedulable** (do NOT proceed until this shows `1`):

```bash
kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}{"\n"}'
# → 1   (if empty, the runtime/device-plugin wiring is wrong — fix before installing Keos)
```

## 3 · Install Keos (GPU on, local ARM64 build)

```bash
git clone <your keos repo> && cd keos/sre-agent
bash scripts/install-edge.sh --local-build --gpu
```

What this does on the Jetson:
- detects k3s → uses `/run/k3s/containerd/containerd.sock`
- builds the 3 images natively for ARM64 (⏳ the `uv pip install` layer is the
  slow part — minutes on the board; that's expected, not a hang)
- sets `ollama.gpu.enabled=true` + `runtimeClassName=nvidia` + the
  `nvidia.com/gpu` limit on the ollama pod
- pulls the model + seeds the RAG corpus

```bash
keosctl status        # 13/13 pods Ready
keosctl doctor        # models present, runbook corpus seeded
```

## 4 · ✅ Verify the inference is actually on the GPU

This is the whole point — three independent checks:

**a) ollama reports the model on GPU:**
```bash
ollama_pod=$(kubectl -n keos get pod -l app.kubernetes.io/component=ollama -o jsonpath='{.items[0].metadata.name}')
kubectl -n keos exec "$ollama_pod" -- ollama ps
# PROCESSOR column must say "100% GPU" (not "100% CPU")
```

**b) ollama's logs show layer offload to CUDA:**
```bash
kubectl -n keos logs "$ollama_pod" | grep -iE "offload|CUDA|layers"
# look for:  load_tensors: offloaded N/N layers to GPU   +   device CUDA0
```

**c) Watch the GPU light up during a real inference + measure tok/s.**
In one terminal:
```bash
sudo tegrastats --interval 500     # watch GR3D_FREQ (GPU%) jump during inference
```
In another, fire a real security incident and time the graph:
```bash
keosctl mode active --yes
keosctl run lib-reverse-shell
keosctl events --domain security -w     # incident should complete in SECONDS on GPU
```
On the Orin GPU, qwen2.5:3b-q4 should give **tens of tok/s** and a full
graph run (2 LLM calls + verify) in well under a minute. On CPU it's minutes
and may time out — so if `events` stays empty for minutes, you're on CPU
(see troubleshooting).

Direct latency probe (from inside the cluster):
```bash
api_pod=$(kubectl -n keos get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}')
kubectl -n keos exec "$api_pod" -- sh -c '
  curl -s http://ollama:11434/api/generate \
    -d "{\"model\":\"qwen2.5:3b-instruct-q4_K_M\",\"prompt\":\"say ok\",\"stream\":false,\"options\":{\"num_predict\":64}}" ' \
  | python3 -c 'import json,sys;d=json.load(sys.stdin);print("tok/s:",round(d["eval_count"]/(d["eval_duration"]/1e9),1))'
```

## 5 · Troubleshooting (the failure modes you'll actually hit)

| Symptom | Cause | Fix |
|---|---|---|
| `nvidia.com/gpu` allocatable is empty | device plugin / nvidia runtime not wired into k3s containerd | redo step 2; check `kubectl -n kube-system logs ds/nvidia-device-plugin-daemonset` |
| `ollama ps` says **100% CPU** | ollama can't see the GPU in the pod (runtimeClass not applied, or toolkit/JetPack mismatch) | confirm the ollama pod has `runtimeClassName: nvidia` + the GPU limit; `kubectl -n keos describe pod $ollama_pod` |
| ollama **HTTP 500 on generate** but model "loads" | model blob vs. ollama runtime mismatch (the bug we hit on the dev box) | re-pull fresh: `kubectl -n keos exec $ollama_pod -- ollama pull qwen2.5:3b-instruct-q4_K_M`; if still 500, pin an ollama image version known-good for your JetPack |
| Incident never completes / `events` empty for minutes | inference on CPU (slow) or timing out | verify step 4; as a guaranteed fallback reinstall with `--no-gpu` (CPU works, just slow) |
| Build "hangs" on `uv pip install` | normal — slow ARM64 dep install | wait it out, or pre-build a bundle and `--airgap` |
| OOM on Orin Nano 8 GB | full stack + model too heavy | use the 3B model only, `--no-gpu` for other stores, or drop chaos-mesh (`--set chaos-mesh.enabled=false`) |

## 6 · Model choice

| Board RAM | Model (`OLLAMA_CHAT_MODEL`) | Notes |
|---|---|---|
| 8 GB (Orin Nano) | `qwen2.5:3b-instruct-q4_K_M` | ~2 GB on GPU; leaves headroom for the rest of the stack |
| 16 GB (Orin NX) | `qwen2.5:7b-instruct-q4_K_M` | ~4.7 GB on GPU; noticeably better diagnosis quality |

Override at install:
`bash scripts/install-edge.sh --local-build --gpu` then
`kubectl -n keos set env deploy/api OLLAMA_CHAT_MODEL=qwen2.5:7b-instruct-q4_K_M`
(+ `keosctl restart api --wait`), after `ollama pull`-ing the bigger model.

---

**TL;DR for tomorrow:** k3s → wire the nvidia runtime + device plugin into
k3s containerd (verify `nvidia.com/gpu = 1`) → `install-edge --local-build
--gpu` → confirm `ollama ps` says **100% GPU** and a `lib-reverse-shell`
incident completes in seconds. If GPU won't cooperate, `--no-gpu` always
works while you sort out the JetPack/ollama version pairing.
