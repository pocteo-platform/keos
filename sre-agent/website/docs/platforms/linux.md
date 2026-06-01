---
id: linux
slug: /platforms/linux
title: Linux (Docker Engine)
sidebar_position: 1
description: Install the Keos SRE Agent on a native Linux host using Docker Engine (no Docker Desktop required).
---

# Linux (Docker Engine)

The Keos SRE Agent runs on any modern Linux distro with Docker Engine
24+. This path uses **native Docker Engine** — no Docker Desktop, no
VM layer. Lighter than the macOS / Windows path and the most common
production-laptop choice.

Tested on Ubuntu 22.04 / 24.04, Debian 12, Fedora 40, RHEL 9.

## Prerequisites

```bash
# 1. Docker Engine (rootful — the install script auto-detects)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker          # re-login OR open a new shell

# 2. Python 3.11 or 3.12
sudo apt install -y python3 python3-pip python3-venv      # Debian/Ubuntu
# or
sudo dnf install -y python3 python3-pip                   # RHEL/Fedora

# 3. kubectl
sudo curl -fsSL -o /usr/local/bin/kubectl \
    https://dl.k8s.io/release/v1.31.0/bin/linux/amd64/kubectl
sudo chmod +x /usr/local/bin/kubectl

# 4. Git + make are usually already there; if not:
sudo apt install -y git make
```

Verify Docker works without sudo:

```bash
docker run --rm hello-world
```

If you get a permission-denied socket error, your shell hasn't picked
up the new `docker` group membership — `newgrp docker` or log out + in.

## Resource budget

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 6 GB free | 8 GB free |
| Disk | 5 GB | 10 GB (room for models + audit log growth) |
| CPU | 4 cores | 8+ cores (faster LLM) |

## Install

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent
make install
```

Total time on a cold install: **~10 minutes** (most of it is downloading
the LLM models: `qwen2.5:3b` is ~1.9 GB, `nomic-embed-text` is ~270 MB).

`make install` runs in order:

1. Auto-downloads `uv`, `k3d`, `opa`, `helm` to `~/.local/bin` (no sudo)
2. Generates a random bearer token into `.env.local`
3. Pulls the LLM models into Ollama
4. `docker compose up -d` for the 10-container stack
5. `k3d cluster create` + applies RBAC, CRDs, demo workloads, Chaos Mesh
6. Seeds 8 SRE runbooks into Qdrant
7. Runs `make doctor` for the green-light check

## Verify

```bash
make doctor
```

Expected output:

```
[OK]  OLLAMA       models=['nomic-embed-text', 'qwen2.5']
[OK]  QDRANT       points=8
[OK]  NATS         messages=…, last_seq=…
[OK]  OPA          status=200
[OK]  CLUSTER      nodes=1, first_node=k3d-poc-server-0
[OK]  AGENT_MODE   mode=observe-only
```

Open **http://localhost** — paste the token from `cat .env.local` into
the SPA. Docs site is at **http://localhost:3000**.

## Common Linux-specific gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `permission denied while trying to connect to the Docker daemon socket` | User not in `docker` group | `sudo usermod -aG docker $USER` then `newgrp docker` |
| `port is already allocated: 0.0.0.0:80` | Something else has port 80 (nginx, apache) | `sudo lsof -i :80` then stop the offender, OR set `CADDY_HTTP_PORT=8080` in `.env.local` |
| `kubectl: command not found` after install | `kubectl` not on PATH | Step 3 above; or `apt install kubectl` if your repo carries it |
| Ollama pull fails behind corporate proxy | `HTTPS_PROXY` not exported | `export HTTPS_PROXY=http://proxy:8080` before `make install` |
| `cgroup` related errors at k3d cluster create | Old kernel without cgroup v2 | Upgrade kernel or switch to a distro shipping cgroup v2 (Ubuntu 22+, Fedora 36+, Debian 12+) |

## Native Docker vs Docker Desktop

You **can** run Docker Desktop on Linux, but native Docker Engine has
fewer moving parts: no VM, no licence, less RAM overhead. Stick with
native unless you have a specific Desktop-only feature you need.

If you do use Docker Desktop on Linux, the install path is identical —
just make sure the daemon is running and your user has access to
`/var/run/docker.sock`.

## Next steps

- [Quickstart](../getting-started/quickstart) — three commands, run a scenario
- [Architecture](../architecture/overview) — what's running where
- [Kubernetes via Helm](kubernetes-helm) — production deploy on any conformant cluster
