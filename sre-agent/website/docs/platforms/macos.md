---
id: macos
slug: /platforms/macos
title: macOS (Docker Desktop)
sidebar_position: 2
description: Install the Keos SRE Agent on macOS using Docker Desktop. Covers both Intel and Apple Silicon (M1/M2/M3/M4).
---

# macOS (Docker Desktop)

Works on both **Intel** and **Apple Silicon** (M1 / M2 / M3 / M4)
Macs. The multi-arch images include native arm64 builds, so on Apple
Silicon there's no Rosetta 2 translation overhead.

Tested on macOS 13 Ventura, 14 Sonoma, 15 Sequoia.

## Prerequisites

### 1. Docker Desktop

Download from <https://www.docker.com/products/docker-desktop> and
install. After the first launch:

- **Settings → Resources → Memory**: set to **at least 6 GB** (8 GB
  recommended). The default 2 GB will OOM during model load.
- **Settings → Resources → CPUs**: leave at the default (≥ 4).
- **Settings → Resources → Disk image size**: ≥ 32 GB. Models + the
  k3d cluster's persistent volumes need room.

Verify Docker is running:

```bash
docker run --rm hello-world
```

The whale icon in the menu bar should be steady, not animating.

### 2. Homebrew + everything else

```bash
# Homebrew (if you don't have it)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Python 3.11 or 3.12
brew install python@3.12

# kubectl + git + make
brew install kubectl git make
```

Verify:

```bash
python3 --version    # >= 3.11
kubectl version --client
docker --version
```

## Resource budget

| Resource | Minimum | Recommended |
|---|---|---|
| RAM (allocated to Docker) | 6 GB | 8 GB |
| Disk | 10 GB free | 20 GB |
| CPU cores (allocated to Docker) | 4 | 6+ |

On Apple Silicon with the unified memory architecture, the LLM call
latency is significantly better than on Intel Macs — first scenario
runs in ~2 minutes instead of ~5.

## Install

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent
make install
```

`make install` auto-downloads `uv`, `k3d`, `opa`, `helm` to
`~/.local/bin`. Make sure `~/.local/bin` is on your `PATH` — add to
`~/.zshrc` if not:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Total cold-install time: **~8 minutes** (Apple Silicon) / **~10 minutes**
(Intel).

## Verify

```bash
make doctor
```

All checks should be `[OK]`. Open **http://localhost** in your
browser, paste the token from `cat .env.local`. Docs site is at
**http://localhost:3000**.

## Common macOS-specific gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `Cannot connect to the Docker daemon` | Docker Desktop not running | Open the Docker Desktop app, wait for whale icon to be steady |
| `port 80 is already in use` | macOS's stock httpd, or another app | `sudo lsof -i :80` then stop with `sudo launchctl unload -w /System/Library/LaunchDaemons/org.apache.httpd.plist` |
| OOM during `make pull-models` | Docker memory limit too low | Settings → Resources → Memory → 8 GB → Apply & Restart |
| Slow LLM (>10 min for one scenario) | CPU constrained | Settings → Resources → CPUs → max → Apply & Restart. On Intel Macs, ~5 min is normal |
| `:6550` won't bind on Apple Silicon | Lingering port lease from a previous k3d cluster | `make cluster-down` then `make cluster` |
| `arm64 vs amd64` warnings during pull | Stale image manifest cache | `docker compose pull --policy always` |
| Filesystem case-sensitivity errors | Some Macs format their dev volume case-insensitive | Create a case-sensitive APFS volume for the repo: Disk Utility → File → New Image |

## Apple Silicon notes

The Keos images are **multi-arch** (linux/amd64 + linux/arm64) so
Docker Desktop pulls the native arm64 variant on M-series Macs. No
emulation overhead.

GPU acceleration via the M-series Neural Engine is NOT supported by
Ollama yet (as of Q2 2026). If you want GPU, run the agent on a
Jetson — see [Jetson](jetson). On Apple Silicon, the CPU is fast
enough for a comfortable demo loop.

## Next steps

- [Quickstart](../getting-started/quickstart) — three commands, run a scenario
- [Architecture](../architecture/overview) — what's running where
- [Kubernetes via Helm](kubernetes-helm) — production deploy
