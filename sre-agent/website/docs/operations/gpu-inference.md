---
id: gpu-inference
slug: /operations/gpu-inference
title: GPU inference (NVIDIA)
sidebar_position: 6
description: Run Ollama on an NVIDIA GPU instead of the CPU. ~50× faster LLM calls; the agent loop becomes interactive.
---

# GPU inference (NVIDIA)

By default the laptop demo runs Ollama on CPU. First-ever LLM call
takes 5-6 minutes (cold-load); subsequent calls take ~30-60 seconds.
With an NVIDIA GPU available, the same model lands ~50× faster:

| Metric | CPU | RTX 3070 (8 GB VRAM) |
|---|---|---|
| Cold-load | ~5-6 min | ~3 s |
| Warm diagnose call (120 tokens) | ~60 s | ~1.3 s |
| Subsequent calls (5 tokens) | ~30 s | ~70 ms |
| Whole scenario (diagnose + plan + verify) | ~10 min | **~3-5 s** |

The agent loop becomes interactive — Phase 12 calibration becomes
genuinely useful because you can run a hundred scenarios in a minute
and build real statistics.

## Prerequisites

- **NVIDIA GPU** with ≥ 4 GB VRAM. The `qwen2.5:3b-instruct-q4_K_M`
  model weighs ~1.9 GB + Ollama overhead → ~2.5 GB resident.
  RTX 3050 8GB, RTX 3060, RTX 3070 Laptop, RTX 4060+, A2000, etc.
  all work comfortably.
- **NVIDIA driver R535+** on the Windows / Linux host.
- **Docker Desktop with GPU support enabled** (Windows) OR the
  NVIDIA Container Toolkit (`apt install nvidia-container-toolkit`)
  on Linux.

Smoke-test before running the agent:

```bash
docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi
```

If that prints your GPU, you're ready. If not:
- **Windows**: Docker Desktop → Settings → Resources → "Enable GPU
  support". May require Docker Desktop 4.x+ and the host's NVIDIA
  driver up to date.
- **Linux**: install `nvidia-container-toolkit` and restart the
  Docker daemon. See the
  [NVIDIA install guide](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).

## Running with GPU

There's a single overlay file (`docker-compose.gpu.yml`) that layers on
top of the base compose file. Two shortcut targets ship in the
Makefile:

```bash
# Linux / macOS / WSL
make up-gpu

# Windows PowerShell
.\make.ps1 up-gpu
```

Both:

1. Run the smoke test (`docker run --gpus all nvidia/cuda nvidia-smi`)
   and fail fast if GPU passthrough isn't working.
2. Bring up the full stack with the overlay.

Verify Ollama detected the GPU after start:

```bash
docker logs shp-ollama | grep -E "library=cuda|inference compute"
```

Expected output (RTX 3070 example):

```
inference compute … library=cuda variant=v12 compute=8.6
name="NVIDIA GeForce RTX 3070 Laptop GPU" total="8.0 GiB" available="7.0 GiB"
```

## What the overlay actually does

The overlay is small. Three changes to the `ollama` service:

- `deploy.resources.reservations.devices` claims all visible NVIDIA
  GPUs. Ollama auto-detects + uses them; no app config change.
- `OLLAMA_KEEP_ALIVE=30m` keeps the model resident between calls
  (default is 5 min). With GPU memory cheap to leave occupied,
  the longer keep-alive means zero cold-load penalty between
  back-to-back scenarios.
- `OLLAMA_NUM_PARALLEL=2` lets `diagnose` and `plan_action` overlap
  when two events queue up.
- `OLLAMA_CONTEXT_LENGTH=4096` trims VRAM overhead from the default
  8192 → fits comfortably on cards with 8 GB or less.

The base `docker-compose.yml` is untouched. Drop the overlay and
you're back to CPU.

## Going back to CPU

```bash
docker compose down
make up      # or: make.ps1 up
```

The base compose file doesn't request GPU resources, so Ollama
runs on CPU again. Cached models work either way (the Ollama runtime
picks the inference backend at load time).

## Upgrading to qwen2.5:7b

If you have ≥ 8 GB VRAM and want higher diagnostic accuracy, the
post-Phase-16 fallback in the roadmap is to bump the model. With
GPU there's no reason not to:

```bash
docker exec shp-ollama ollama pull qwen2.5:7b-instruct-q4_K_M
```

Then set the env in `.env`:

```
OLLAMA_CHAT_MODEL=qwen2.5:7b-instruct-q4_K_M
```

Expected confidence lift: +3-5 percentage points on the median (per
Phase 12 measurements). 7b q4_K_M is ~4.4 GB resident; fits in 8 GB
with room for the embed model + KV cache.

## Common GPU-specific gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `make up-gpu` fails at smoke-test | Docker Desktop GPU support not enabled | Settings → Resources → Enable GPU support; restart Docker Desktop |
| Ollama starts but logs `inference compute … library=cpu` | Docker `--gpus all` wasn't applied | `docker compose -f docker-compose.yml -f docker-compose.gpu.yml config | grep -A4 devices` should show the reservation |
| `CUDA out of memory` on a small VRAM card | Trying to load qwen2.5:7b on < 6 GB | Stay on 3b OR lower `OLLAMA_CONTEXT_LENGTH` further (2048) |
| First-ever call is slow even on GPU | Model not yet pulled into the volume | `make pull-models` to fetch; the load + first call still pays ~3 s once |
| Power throttling on a laptop | Battery saver throttles the dGPU | Plug in; Windows Power → "Best performance"; check `nvidia-smi` Pwr is at TDP |

## Production / edge GPU

For the in-cluster (non-laptop) edge path — Jetson Orin Nano,
NVIDIA-enabled server nodes — the Helm chart already has the
`ollama.gpu.*` block:

```yaml
ollama:
  gpu:
    enabled: true
    resourceName: "nvidia.com/gpu"
    count: 1
    runtimeClassName: "nvidia"
```

See [Jetson](../platforms/jetson) for the full edge GPU walkthrough.
This page is the laptop-Docker-Desktop equivalent.
