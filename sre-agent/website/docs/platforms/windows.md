---
id: windows
slug: /platforms/windows
title: Windows (Docker Desktop + WSL2)
sidebar_position: 3
description: Install the Keos SRE Agent on Windows 10/11 using Docker Desktop with the WSL2 backend.
---

# Windows (Docker Desktop + WSL2)

Two equally-supported paths on Windows:

- **WSL2 path (recommended)** — run the install + agent inside an
  Ubuntu-on-WSL2 distro. Same UX as native Linux; the project's
  `Makefile` is the entry point.
- **PowerShell path** — run from native Windows PowerShell. The
  project ships a `make.ps1` wrapper that mirrors every `make` target.

Both share Docker Desktop's WSL2 backend, so the actual containers run
in the same place.

Tested on Windows 11 24H2 and Windows 10 22H2.

## Prerequisites (one-time setup)

### 1. WSL2 + Ubuntu

```powershell
# Open PowerShell as Administrator
wsl --install -d Ubuntu-24.04
# Reboot when prompted.
# On first launch, set a Linux username + password.
```

If WSL is already installed, verify:

```powershell
wsl --list --verbose
# NAME           STATE     VERSION
# Ubuntu-24.04   Running   2
```

The `VERSION` column **must** be 2. If you see 1, run
`wsl --set-version Ubuntu-24.04 2`.

### 2. Docker Desktop

Download from <https://www.docker.com/products/docker-desktop> and
install. After install:

- **Settings → General**: ✅ Use the WSL 2 based engine.
- **Settings → Resources → WSL integration**: ✅ Enable for your
  Ubuntu-24.04 distro.
- **Settings → Resources → Advanced**: Memory ≥ 8 GB, CPUs ≥ 4.

Restart Docker Desktop. Verify from inside WSL:

```bash
wsl -d Ubuntu-24.04 -- docker run --rm hello-world
```

### 3. PowerShell execution policy (for `.\make.ps1`)

Fresh Windows blocks unsigned scripts. Open **PowerShell as
Administrator** once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

### 4. Port 80 must be free

Caddy binds port 80. Common conflicts: IIS, Skype, Apache, OneDrive's
reporting service.

```powershell
Get-NetTCPConnection -LocalPort 80 -ErrorAction SilentlyContinue
# Empty result = port is free.
```

If something has it:
- IIS: `Stop-Service W3SVC` (or set to `Disabled` so it doesn't auto-restart)
- Otherwise: `netstat -aon | findstr :80` then `taskkill /PID <pid> /F`
- Or set `CADDY_HTTP_PORT=8080` in `.env.local` after install (use port 8080).

## Install — option A: WSL2 (recommended)

```bash
# Inside WSL2: Ubuntu-24.04
wsl -d Ubuntu-24.04

# Now you're in Linux. Install minimal extras:
sudo apt update && sudo apt install -y python3 python3-pip git make curl

# Clone + install
git clone https://github.com/pocteo-platform/keos
cd keos/sre-agent
make install
```

`make install` auto-downloads `uv`, `k3d`, `opa`, `helm` to
`~/.local/bin` (inside WSL).

Total cold-install time: **~10 minutes** (model download dominates).

## Install — option B: native PowerShell

```powershell
git clone https://github.com/pocteo-platform/keos
cd keos\sre-agent
.\make.ps1 install
```

`make.ps1` mirrors every `make` target. It auto-installs `uv`, `k3d`,
`opa.exe` (Windows binary) under `bin\` and `%USERPROFILE%\.local\bin`.

## Verify

```bash
# WSL
cd ~/keos/sre-agent && make doctor
```

```powershell
# PowerShell
cd keos\sre-agent
.\make.ps1 doctor
```

All checks should be `[OK]`. Open **http://localhost** — paste the
token from:

```bash
cat .env.local        # WSL
```

```powershell
Get-Content .env.local   # PowerShell
```

Docs site is at **http://localhost:3000**.

## Common Windows-specific gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `docker: error during connect` | Docker Desktop not running | Start it; wait for whale icon to be steady |
| `make.ps1 cannot be loaded ... execution policy` | Windows blocks unsigned scripts | See "PowerShell execution policy" above |
| `Error response from daemon: ports are not available: 0.0.0.0:80` | Port 80 in use | See "Port 80 must be free" above |
| `wsl: The system cannot find the file specified` | WSL not installed or wrong version | `wsl --install -d Ubuntu-24.04` |
| Files in WSL appear with `mode 0777` | WSL on `/mnt/c/...` Windows-mount | Clone the repo inside WSL (`~/keos`), not on `/mnt/c/...` — much faster, correct perms |
| LLM model pull fails behind corporate proxy | `HTTPS_PROXY` not exported in WSL | `export HTTPS_PROXY=http://proxy:8080` before `make install` |
| `kubectl: command not found` on PowerShell | kubectl not installed | `winget install Kubernetes.kubectl` |
| Hyper-V conflict warning on `wsl --install` | VirtualBox / VMware also installed | Disable nested virt in those, OR uninstall them |

## WSL2 + Windows hybrid: which side runs what?

- **Docker containers** run inside Docker Desktop's WSL2 distro
  (managed for you).
- **The agent process** (when running via `make demo` for one-shot use)
  runs inside your Ubuntu-24.04 WSL2 distro.
- **`kubectl`, `docker`, `make`** can be invoked from either side —
  Docker Desktop bridges them.
- **The SPA + docs sites** are served on `localhost` and reachable
  from both Windows browsers and WSL2 curl.

## Next steps

- [Quickstart](../getting-started/quickstart) — three commands, run a scenario
- [Architecture](../architecture/overview) — what's running where
- [Kubernetes via Helm](kubernetes-helm) — production deploy
