---
id: jetson-kickoff
slug: /platforms/jetson-kickoff
title: Jetson kickoff — hand this to Claude Code on the device
sidebar_position: 7
description: A self-contained kickoff brief for a fresh Claude Code session running ON a Jetson Orin. Paste the brief, and the agent walks the GPU-inference install + security validation from scratch.
---

# Jetson kickoff — hand this to Claude Code on the device

This page exists so a **fresh Claude Code session running on the Jetson**
has everything it needs without the context of the session that set this up.
The goal is narrow: **install Keos, prove the LLM runs on the GPU, and
validate the security self-healing flow end-to-end.**

## 1 · Install Claude Code on the Jetson

```bash
# Node 20 (Claude Code needs 18+), ARM64
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs git
npm install -g @anthropic-ai/claude-code

# Clone the repo (PRIVATE — needs a GitHub PAT over HTTPS or an SSH deploy key)
git clone https://github.com/pocteo-platform/keos.git ~/keos
cd ~/keos/sre-agent
claude
```

## 2 · Paste this as your first message to Claude Code

> I'm on an NVIDIA Jetson Orin running Ubuntu 24 (JetPack 6.x). **Goal:
> install Keos and prove the ollama LLM runs on the GPU**, then validate the
> security self-healing flow end-to-end. The repo is cloned at `~/keos`;
> you're in `~/keos/sre-agent`.
>
> **Follow the runbook `website/docs/platforms/jetson-ubuntu24-gpu.md`** — it
> is the exact procedure. Sequence:
>
> 1. **Recon:** `nvidia-smi` (or `sudo tegrastats`), `cat /etc/nv_tegra_release`, `free -h`.
> 2. **k3s:** `curl -sfL https://get.k3s.io | sh -`, then set up `~/.kube/config`
>    from `/etc/rancher/k3s/k3s.yaml`.
> 3. **GPU enablement (the crux):** install the NVIDIA Container Toolkit, register
>    the `nvidia` runtime in **k3s's own containerd** config, and install the
>    NVIDIA k8s device plugin. Then verify:
>    `kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'`
>    must return `1`. **Do not proceed until it does.**
> 4. **Install:** `bash scripts/install-edge.sh --local-build --gpu`
>    (private images → `--local-build`; the ARM64 build is slow but expected,
>    not a hang).
> 5. **Prove GPU inference:** `ollama ps` must say **`100% GPU`** (not CPU);
>    the ollama logs show `offloaded N/N layers to GPU` + `device CUDA0`;
>    `sudo tegrastats` GR3D_FREQ spikes during a generate; measure tok/s.
> 6. **Validate the security loop:** `keosctl mode active` then
>    `keosctl run lib-reverse-shell`. The incident must complete in **seconds**
>    (not minutes — that's the whole point of the GPU), and `isolate_pod` must
>    actually contain a real `nginx-demo` pod: it labels the pod
>    `keos.pocteo/quarantine=true` and creates a deny-all NetworkPolicy. The
>    installer seeds those demo workloads automatically now.
>
> **Known gotcha:** ollama may return HTTP 500 on generate, or silently fall
> back to CPU, if the JetPack ↔ CUDA ↔ ollama versions don't line up. If so:
> re-pull the model fresh (`kubectl -n keos exec <ollama-pod> -- ollama pull
> qwen2.5:3b-instruct-q4_K_M`) and/or pin a JetPack-compatible ollama image.
> `--no-gpu` is the guaranteed-working CPU fallback while you sort the pairing.
>
> Start by reading `website/docs/platforms/jetson-ubuntu24-gpu.md`, then walk
> the steps. Report tok/s and the final `isolate_pod` result.

## 3 · What's already fixed (so you don't re-debug it)

These landed in the repo before this device was provisioned — they're in the
code you just cloned:

- **NetworkPolicy → kube-apiserver egress** (`networkPolicy.apiServerCIDRs`,
  default private ranges). Without it the agent reads 0 nodes / 0 pods.
- **Demo workloads seeded by the installer** (`workloads`/`nginx-demo`) so
  use cases have a live containment target (`--no-demo-workloads` to opt out).
- **Security triage** never skips a security event as `already_ready`
  (a healthy pod can still be compromised).
- **Ghost-incident suppression** in the observer (no auto-resolved flood from
  recurring kubelet warnings on healthy pods).
- **Falco + Tetragon on by default** — and unlike WSL2, they actually run on
  the Jetson's real eBPF-capable kernel, so you get live runtime detection.
- **`keosctl` PATH wiring + `ui`/`port-forward`** fixes.

## 4 · Memory check (Jetson session)

The Jetson session has its own memory. Worth saving once you've confirmed the
GPU path: the working JetPack/CUDA/ollama version triple, and the measured
tok/s — so the next install on this device doesn't rediscover the pairing.
