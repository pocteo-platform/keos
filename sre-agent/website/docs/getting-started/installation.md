---
id: installation
slug: /getting-started/installation
title: Installation
sidebar_position: 2
description: Prerequisites, OS-specific setup, and the one-command install flow.
---

# Installation

The laptop demo path is documented here. For cluster installs, see
[Platforms → Kubernetes via Helm](../platforms/kubernetes-helm).

## Prerequisites

| Tool | Version | Why | Install |
|---|---|---|---|
| Docker Desktop | 24+ | Container runtime | https://www.docker.com/products/docker-desktop |
| Python | 3.11 or 3.12 | Host-side helpers (`doctor`, `seed_rag`) | https://www.python.org/downloads/ |
| Git | any | clone the repo | https://git-scm.com/downloads |
| kubectl | ≥ 1.27 | talk to the cluster | `winget install Kubernetes.kubectl` / `brew install kubectl` / `apt install kubectl` |
| Make | any | run `make <target>` | macOS/Linux: built-in. Windows: use `.\make.ps1` instead. |

The `install` target downloads everything else automatically into
`~/.local/bin` (no sudo): `uv` (Python deps), `k3d` (laptop K8s
cluster), `opa` (policy engine), `helm` (Chaos Mesh install), plus the
LLM models into a Docker volume.

### Windows one-time setup

1. **Docker Desktop must be RUNNING** before `make install`. Whale icon
   in the system tray = running. If it just installed, sign in + restart it.
2. **Allow `.\make.ps1` to execute** (fresh Windows blocks unsigned scripts).
   Open PowerShell **as Administrator** and run once:
   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
   ```
3. **Port 80 must be free** — Caddy binds it. Common conflicts: IIS, Skype,
   Apache, OneDrive's reporting. Check with:
   ```powershell
   Get-NetTCPConnection -LocalPort 80 -ErrorAction SilentlyContinue
   ```
   If something's there, stop the service (`Stop-Service W3SVC` for IIS).

### WSL / Linux

`make install` auto-installs all prereqs to `~/.local/bin`. Make sure
`$HOME/.local/bin` is on your `PATH` (most modern distros do this in
`~/.profile`).

## Install

```bash
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent

# Linux / macOS / WSL
make install

# Windows PowerShell
.\make.ps1 install
```

Total time on a cold machine: **~10 minutes** (most of it is the LLM
model download, ~2.2 GB combined). Subsequent installs skip cached
steps.

## What `install` does

1. **Prereqs**: ensures `uv`, `k3d`, `opa`, `helm` are present
   (downloads to `~/.local/bin` if missing — no sudo needed).
2. **Token**: generates a random `SHP_API_TOKEN` into `.env.local`.
3. **Models**: pulls `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB) and
   `nomic-embed-text` (~270 MB) into Ollama.
4. **Compose**: brings up the 9 containers — Ollama, Qdrant, NATS,
   OPA, K8sGPT, MCP server, API, frontend, Caddy.
5. **Cluster**: creates the k3d cluster, applies RBAC + CRDs + demo
   workloads, Helm-installs Chaos Mesh.
6. **Seed**: embeds the 8 SRE runbooks into the Qdrant RAG store.
7. **Doctor**: probes every component and prints `[OK]` / `[--]` per
   dependency. Re-runnable any time.

Steps 1–3 only run on the first install; subsequent runs skip them.

## Verify

```bash
make doctor          # Linux / macOS / WSL
.\make.ps1 doctor    # Windows
```

Expected output:

```
================================================================
  Keos SRE Agent — System Doctor
================================================================
[OK]  OLLAMA       models=['nomic-embed-text', 'qwen2.5']
[OK]  QDRANT       points=8
[OK]  NATS         messages=12, last_seq=12
[OK]  OPA          status=200
[OK]  CLUSTER      nodes=1, first_node=k3d-poc-server-0
[OK]  AGENT_MODE   mode=observe-only

All systems go. You can run a use case from the UI.
```

Open **http://localhost** in your browser. On first launch you'll see a
token prompt. Get the token with:

```powershell
# Windows
Get-Content .env.local
```

```bash
# macOS / Linux
cat .env.local
```

Copy the value after `SHP_API_TOKEN=` and paste it. The token persists
in browser localStorage; you won't be asked again.

## When `install` goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `docker: error during connect` | Docker Desktop not running | Start Docker Desktop, wait for the whale icon, retry |
| `make.ps1 cannot be loaded ... execution policy` | Windows blocks unsigned scripts | See Windows one-time setup above |
| `Error response from daemon: ports are not available: 0.0.0.0:80` | Port 80 in use | Stop the conflicting service |
| Browser shows "Unable to connect" at http://localhost | Caddy didn't start or firewall | `docker compose logs caddy` |
| Scenario runs but never completes | LLM cold-start on first call (~60 s) OR model not loaded | Wait 60 s; if stuck, `docker compose logs ollama` |
| `Ollama model pull fails` | Network / corporate proxy | Set `HTTPS_PROXY` in your shell before `make install` |
| `k3d cluster create poc` hangs | Docker low on memory | Increase Docker Desktop memory to ≥ 8 GB in Settings → Resources |

Full reset (drops models — next install re-downloads):

```bash
make clean
make install
```

Next: [Quickstart](quickstart).
