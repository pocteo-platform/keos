---
id: installation
slug: /installation
title: Installation
sidebar_position: 3
description: Install Keos on any conformant Kubernetes — Linux (all distros), amd64 + arm64, k3s / Talos / Kairos / kubeadm / k3d, with GPU, from a private registry, or fully air-gapped.
---

# Installation

Keos installs as one Helm chart (`pocteo`) via **`keosctl`**. The same flow
covers a laptop demo, a single-node edge box, a Jetson with a GPU, and a
fully-disconnected fleet site. Pick the path that matches your target.

> **One entry point.** Everything is `keosctl`. The interactive
> `keosctl install` wraps the underlying scripts; you never run them directly.

## Prerequisites

| Tool | Needed for |
|---|---|
| `kubectl` ≥ 1.27 | all targets |
| `helm` ≥ 3.13 | chart install |
| `openssl` | generating auth + HMAC secrets |
| a reachable cluster | `kubectl get nodes` must work from where you run `keosctl` |
| `docker` (+ buildx) | only for `--local-build` or building a private registry |

Run a pre-flight first — it checks binaries, cluster reachability, distro,
containerd socket, node arch vs. images, memory vs. model, GPU, kernel BTF, and
registry reachability, each with a one-line remedy:

```bash
keosctl preflight            # add --gpu on a GPU node, --image-registry <host:5000> for a fleet
```

## Quick start (any cluster with internet)

```bash
git clone <repo> && cd sre-agent
keosctl install              # interactive wizard: cluster · model · images · mode · security
keosctl ui                   # port-forward + open the SPA; paste the printed token
```

Headless / scripted:

```bash
keosctl install --headless --mode observe-only           # safe default
keosctl install --headless --gpu --security              # GPU + live eBPF sensors
```

`keosctl install` auto-detects the distro and sets the right containerd socket,
GPU runtimeClass, and security tier. It seeds demo workloads (`nginx-demo`) so
the use-case library has a live target.

## Architectures (amd64 + arm64)

First-party images are published **multi-arch**; the node's containerd pulls
the right one automatically. On ARM64 with a *private* registry you must publish
arm64 images (see [Operations](./OPERATIONS.md) — `keosctl release`). If public
images aren't reachable, `keosctl install` falls back to `--local-build`
(builds on the node; needs docker + a writable host — not possible on
Talos/Kairos).

## Per-target paths

### Linux distros / generic Kubernetes

Any conformant cluster works. The installer detects the containerd socket:

| Distro | containerd socket |
|---|---|
| Talos | `/run/containerd/containerd.sock` |
| k3s / Kairos / RKE2 | `/run/k3s/containerd/containerd.sock` |
| kubeadm / generic | `/run/containerd/containerd.sock` |
| k3d / kind (dev) | the cluster's own runtime |

```bash
export KUBECONFIG=~/.kube/config
keosctl install --headless --mode observe-only
```

### k3s on an edge node (Linux, Raspberry Pi, generic ARM/AMD)

```bash
curl -sfL https://get.k3s.io | sh -        # if not already running
mkdir -p ~/.kube && sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown "$USER" ~/.kube/config && export KUBECONFIG=~/.kube/config
keosctl install
```

### Talos / Kairos (immutable edge OS — recommended for fleets)

Both ship containerd + the NVIDIA toolkit (on Jetson) and are managed from a
workstation (Talos via `talosctl`, Kairos via cloud-init). Fetch the kubeconfig,
then install:

```bash
# Talos:  talosctl --nodes <ip> kubeconfig ~/.kube/keos.yaml
# Kairos: ssh kairos@<ip> 'sudo cat /etc/rancher/k3s/k3s.yaml' | sed "s/127.0.0.1/<ip>/" > ~/.kube/keos.yaml
export KUBECONFIG=~/.kube/keos.yaml
keosctl install --headless --gpu        # GPU auto-enabled when nvidia.com/gpu is present
```

On Talos, GPU comes from NVIDIA **system extensions** in the machine config (the
installer only verifies allocatable). On Kairos, the toolkit + CDI spec are
baked into the image recipe (immutable OS — no post-install host changes).

### NVIDIA Jetson (Orin) — GPU inference

The headline edge target. Jetson is fussier than a discrete GPU because the
ecosystem lags the L4T/JetPack release. The validated recipe:

1. **k3s + GPU into containerd.** k3s ≥1.30 auto-registers the `nvidia` runtime.
   Install the NVIDIA device plugin and confirm:
   ```bash
   kubectl get nodes -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}'   # → 1
   ```
2. **CDI fix (Tegra).** On recent JetPack the toolkit needs a CDI `tegra` alias:
   ```bash
   sudo nvidia-ctk cdi generate --mode=csv --output=/etc/cdi/nvidia.yaml
   # append a `tegra` alias device (copy of `all`), then:
   sudo nvidia-ctk config --in-place --set nvidia-container-runtime.mode=cdi
   ```
3. **Install with GPU:**
   ```bash
   keosctl install --headless --gpu
   ```
4. **Prove the GPU:** `keosctl shell ollama` → `ollama ps` must show **`100% GPU`**;
   `tegrastats` GR3D spikes during a generation. (See
   [Operations → GPU](./OPERATIONS.md#gpu-inference) for verification + tok/s.)

> **The known gotcha:** stock `ollama/ollama` images silently fall back to CPU on
> Orin (their arm64 builds omit `sm_87`). Keos uses a Jetson ollama variant
> (`ollama/ollama` + the official `jetpack6` CUDA runner overlay) **plus the env
> `JETSON_JETPACK=6`** — `keosctl install --gpu` wires this when it detects Tegra.
> `--no-gpu` is the guaranteed CPU fallback. An 8 GB Orin needs swap (the
> preflight warns); use the 3B model.

### Private registry (no public pulls, ever)

For security/air-gap, host all images + the OCI chart in a local `:5000`
registry and install from it — no public registry is ever contacted.

```bash
# Workstation: populate the registry (multi-arch images + chart). See Operations.
REGISTRY=registry.fleet.local:5000 keosctl release all [--insecure]

# Node: trust the registry (k3s /etc/rancher/k3s/registries.yaml — see Operations),
#       then install from it:
keosctl install --headless --image-registry registry.fleet.local:5000 [--plain-http] [--gpu]
```

The installer repoints **every** image (first-party + stock + Falco/Tetragon
subcharts) at the private registry.

### Fully air-gapped (no network at all)

Build a single bundle on a connected workstation, carry it to the site:

```bash
keosctl bundle create                          # one tarball: images + chart + models
# at the site: untar, load images into the local :5000 registry, then:
keosctl install --headless --airgap --image-registry registry.fleet.local:5000 --plain-http
```

### GitOps / pull-based updates (air-gapped fleets)

Instead of push-based upgrades, have Flux on the cluster pull desired state from
`:5000`. Bootstrap once, then updates are a registry push + reconcile — no
inbound access. See [Operations → GitOps](./OPERATIONS.md#gitops-pull-based-delivery).

## Security sensors (Falco + Tetragon)

Live eBPF runtime detection is on by default (`--no-security` to skip). It needs
a capable kernel:

- **BTF kernel** (most stock distros ≥5.8, Talos) → Falco 0.45 `modern_ebpf`,
  Tetragon on.
- **No BTF** (Jetson L4T, locked-down kernels) → Falco 0.40 (`falco-legacy`)
  with a legacy probe prebuilt for the node's kernel:
  `sudo REGISTRY=<reg> keosctl falco-probe build` on the node (once per
  kernel), Tetragon off. Falco 0.41+ no longer ships the legacy probe.
- **WSL2 / incapable** → sensors off; the synthetic use-case demo still works.

To add Falco or Tetragon to an existing install, run `keosctl deps install falco`
(or `tetragon`). It uses the chart's versions, and Falco delivers through
falcosidekick with the agent's webhook token.

**Laptop demo (docker compose + k3d):** `make sensors` (`.\make.ps1 sensors` on
Windows) installs cert-manager, Tetragon and Falco 0.45 on the k3d cluster and
points falcosidekick at the compose API. It needs a kernel with BTF (Docker
Desktop's WSL2 kernel has it) and adds `FALCO_WEBHOOK_TOKEN` to `.env.local` if
it's missing.

## Verify the install

```bash
keosctl status                 # 13–15 pods Ready, agent mode, release
keosctl doctor                 # health across every component (+ --e2e for the live security path)
keosctl ui                     # open the SPA, paste the token
```

## Uninstall

```bash
keosctl uninstall              # removes release + PVCs + namespace (confirms first)
keosctl uninstall --keep-pvcs  # keep audit log + vector store
```
