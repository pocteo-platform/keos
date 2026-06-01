# Keos SRE Agent

[![CI](https://github.com/pocteo-platform/keos/actions/workflows/ci.yml/badge.svg)](https://github.com/pocteo-platform/keos/actions/workflows/ci.yml)
[![Docs site](https://github.com/pocteo-platform/keos/actions/workflows/docs.yml/badge.svg)](https://pocteo-platform.github.io/keos/)
[![Release](https://img.shields.io/github/v/release/pocteo-platform/keos?include_prereleases&sort=semver)](https://github.com/pocteo-platform/keos/releases)
[![Status](https://img.shields.io/badge/status-v1.0.0--rc.1-blueviolet)](docs/RELEASE_NOTES_1.0.0-rc.1.md)
[![Docs](https://img.shields.io/badge/docs-pocteo--platform.github.io%2Fkeos-15117A)](https://pocteo-platform.github.io/keos/)
[![Multi-arch](https://img.shields.io/badge/images-linux%2Famd64%20%7C%20linux%2Farm64-informational)](deploy/buildx-bake.hcl)

**The autonomous SRE that ships inside every Keos edge node — by Pocteo Platform.**

**Keos** is [Pocteo Platform](https://github.com/pocteo-platform)'s Kubernetes
edge OS — designed to run on Jetson, Talos, k3s-on-tiny-hardware, or any
conformant cluster via Helm. The **Keos SRE Agent** (this repo) is the
autonomous reliability layer that ships inside every Keos edge node: it
watches the local cluster, diagnoses infrastructure incidents with
retrieval-augmented context, plans a single remediation action, gates it
through Open Policy Agent, executes under scoped RBAC, verifies the cluster
healed, and writes an HMAC-signed audit record.

> The loop: **Observe → Predict → Heal → Learn**.

CPU-only inference, multi-arch images (amd64 + arm64), no GPU dependency.

---

## Before you start (prerequisites)

| Tool | Version | Why | Install |
|---|---|---|---|
| **Docker Desktop** | 24+ | Container runtime for the off-cluster stack | https://www.docker.com/products/docker-desktop |
| **Python** | 3.11 or 3.12 | Host-side helpers (`doctor`, `seed_rag`) | https://www.python.org/downloads/ |
| **Git** | any | clone the repo | https://git-scm.com/downloads |
| **kubectl** | ≥ 1.27 | talk to the cluster | `winget install Kubernetes.kubectl` / `brew install kubectl` / `apt install kubectl` |
| **Make** | any | run `make <target>` | macOS/Linux: built-in. Windows: use `.\make.ps1` instead. |

The `install` target downloads the rest automatically into `~/.local/bin` (no
sudo): `uv` (Python deps), `k3d` (laptop K8s cluster), `opa` (policy engine),
`helm` (for Chaos Mesh install), plus the LLM models into a Docker volume.

### Windows-specific one-time setup

1. **Docker Desktop must be RUNNING** before `make install`. Whale icon in the
   system tray = running. If it just installed, sign in + restart it.
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
   If something's there, stop the service (`Stop-Service W3SVC` for IIS) or
   set `CADDY_HTTP_PORT=8080` in `.env.local` and use `http://localhost:8080`.

### Resource budget

- **Disk**: ~3 GB for the repo + ~2.2 GB for LLM models (in a persistent Docker
  volume, kept across `make down`, dropped by `make clean`).
- **RAM**: 6 GB free for the LLM. Chat model resident is ~2 GB; kept alive
  for 30 min between calls.
- **CPU**: any modern x86 works. First scenario takes 4-5 min on CPU; ~25 s
  on an NVIDIA GPU (see [docs/JETSON.md](docs/JETSON.md)).

---

## Quick Start

**Three commands. ~10 minutes on a cold machine** (mostly model download).

### Windows (PowerShell)

```powershell
git clone https://github.com/your-org/self-healing-poc keos
cd keos
.\make.ps1 install      # prereqs + models + compose + cluster + seed + doctor
.\make.ps1 demo-ui      # opens the SPA, auto-dispatches a scenario
```

### macOS / Linux

```bash
git clone https://github.com/your-org/self-healing-poc keos
cd keos
make install            # same full path
make demo-ui
```

The browser opens **http://localhost**. On first launch you'll see a token
prompt. Get the token with:

```powershell
# Windows
Get-Content .env.local
```
```bash
# macOS/Linux
cat .env.local
```

Copy the value after `SHP_API_TOKEN=` and paste it into the SPA. The token
persists in your browser's localStorage; you won't be asked again.

### What `install` actually does

1. **Prereqs**: ensures `uv`, `k3d`, `opa`, `helm` are present (downloads to
   `~/.local/bin` if missing — no sudo needed).
2. **Token**: generates a random `SHP_API_TOKEN` into `.env.local`.
3. **Models**: pulls `qwen2.5:3b-instruct-q4_K_M` (~1.9 GB) and
   `nomic-embed-text` (~270 MB) into Ollama.
4. **Compose**: brings up Ollama, Qdrant, NATS, OPA, K8sGPT, MCP server,
   API, frontend, and Caddy.
5. **Cluster**: creates the k3d cluster, applies RBAC + CRDs +
   demo workloads, Helm-installs Chaos Mesh.
6. **Seed**: embeds the 8 SRE runbooks into the Qdrant RAG store.
7. **Doctor**: probes every component and prints `[OK]` / `[--]` per
   dependency. Re-runnable any time.

Steps 1-3 only run on the first install; subsequent runs skip them.

---

## Try your first scenario

1. Open **http://localhost**, paste your token (see above).
2. Go to **Control** → flip agent mode from `observe-only` → `active`.
3. Go to **Use Cases** → pick `lib-memory-leak` → click **Run**.
4. Watch the animated **Pipeline** view as the agent walks:
   `triage → retrieve_context → k8sgpt_analyze → diagnose → plan_action →
   policy_gate → execute → verify`.
5. The **Telemetry sidebar** shows pods-ready, restarts, actions taken.
6. The **Incidents** view lists every run with full provenance.

A first scenario takes **4-5 minutes on CPU** (most of it spent in the two
LLM calls — diagnose + plan_action). Subsequent runs are similar; the model
stays resident.

Want to write your own scenario from scratch? See
[docs/USE_CASES.md](docs/USE_CASES.md).

---

## When something goes wrong

### First step: run `doctor`

```powershell
.\make.ps1 doctor      # Windows
make doctor            # macOS/Linux
```

Prints exactly which component is failing and the command to fix it:

```
[OK]  OLLAMA       models=['nomic-embed-text', 'qwen2.5']
[OK]  QDRANT       points=8
[--]  NATS         connection refused
              -> fix: is nats up?
[OK]  OPA          status=200
[OK]  CLUSTER      nodes=1, first_node=k3d-poc-server-0
[OK]  AGENT_MODE   mode=observe-only
```

### Common first-install failures

| Symptom | Cause | Fix |
|---|---|---|
| `docker: error during connect: ...` | Docker Desktop not running | Start Docker Desktop, wait for the whale icon to be steady, retry |
| `make.ps1 cannot be loaded ... execution policy` | Windows blocks unsigned scripts | See "Windows-specific one-time setup" above |
| `Error response from daemon: ports are not available: 0.0.0.0:80` | Port 80 in use | Stop the conflicting service or override the Caddy port (see prereqs) |
| Browser shows "Unable to connect" at http://localhost | Caddy didn't start (port 80 conflict) OR firewall | `docker compose ps caddy` should be `Up`. If not: `docker compose logs caddy` |
| Token prompt rejects what's in `.env.local` | Whitespace/quotes pasted | Paste only the value after `SHP_API_TOKEN=`, no leading/trailing spaces |
| Scenario runs but never completes | LLM cold-start on first call (~30s) OR model not loaded | Wait 60s; if still stuck, `docker compose logs ollama \| tail -50` |
| `Ollama model pull fails` | Network / corporate proxy | Set `HTTPS_PROXY` in your shell before `make install`; or pre-download into the `ollama_models` Docker volume |
| `k3d cluster create poc` hangs | Docker low on memory | Increase Docker Desktop's memory to ≥ 8 GB in Settings → Resources |

If `doctor` is green and the UI still misbehaves, see the runbooks under
[docs/RUNBOOKS/](docs/RUNBOOKS/README.md) — symptom-to-fix decision tree
covering every backend component.

### When all else fails

Full reset:

```powershell
.\make.ps1 clean        # drops volumes including downloaded models
.\make.ps1 install      # full rebuild from scratch
```

---

## Common commands

| Command | What it does |
|---|---|
| `make install` | Full install (prereqs → compose → cluster → seed → doctor) |
| `make doctor` | Diagnose every dependency, print fixes |
| `make demo-ui` | Open the SPA + auto-run scenario 1 |
| `make rebuild` | Rebuild api + frontend + mcp-server after a code change |
| `make seed` | Re-seed the runbook RAG store |
| `make logs` | Tail logs from all compose services |
| `make down` | Stop compose + delete the k3d cluster (keeps models) |
| `make clean` | `down` + drop named volumes (re-download models next install) |
| `make test` | Run the hermetic backend test suite |

Windows users: replace `make` with `.\make.ps1` in every command.

---

## Architecture in 10 seconds

```
infrastructure event (kubelet / chaos / metrics)
  → triage → retrieve_context (RAG) → k8sgpt_analyze (cluster signals)
  → diagnose (LLM, JSON) → plan_action (LLM, constrained catalog)
  → policy_gate (OPA matrix) → execute (scoped kubectl via MCP)
  → verify → END
  |
  └─(low confidence | deny | error | unhealed)→ escalate → END
```

Every step is wrapped in a tamper-evident HMAC audit. Read
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full bible — macro
diagram, per-component Mermaid, end-to-end request lifecycle.

---

## Configuration

Every behavior knob is environment-driven. See
[`.env.example`](.env.example) for a fully-commented template covering
all ~30 variables (auth, endpoints, retention, notifications,
rate-limiting, etc.). Defaults are wired in `agent/config.py`; you only
need to set what you want to override.

Production / Helm-installed deployments configure via
[`deploy/helm/pocteo/values.yaml`](deploy/helm/pocteo/values.yaml) —
see [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) for the full
matrix.

---

## Documentation

| Doc | What's in it |
|---|---|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | **The bible.** Macro + per-part Mermaid diagrams of every component. |
| **[docs/USER_GUIDE.md](docs/USER_GUIDE.md)** | Architecture overview, install paths, configuration, day-2 ops, troubleshooting, security model, FAQ. |
| [docs/USE_CASES.md](docs/USE_CASES.md) | **Hands-on tutorial.** Five end-to-end Chaos Mesh scenarios with step-by-step wizard inputs. |
| [docs/INSTALL.md](docs/INSTALL.md) | Install matrix: Docker Desktop, generic K8s, k3s on Jetson, Talos, air-gapped. |
| [docs/RUNBOOKS/](docs/RUNBOOKS/README.md) | **Incident response.** 10 runbooks + symptom-to-runbook decision tree. |
| [docs/JETSON.md](docs/JETSON.md) | Jetson Orin Nano + k3s + GPU walkthrough. ~25 s graph runs. |
| [docs/TALOS.md](docs/TALOS.md) | Talos-specific notes (containerd socket, machine config, image cache). |
| [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) | Every env var + values.yaml key documented. |
| [docs/UI_GUIDE.md](docs/UI_GUIDE.md) | Web UI walkthrough with screenshots. |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Prometheus metrics + alert rules + LogQL recipes. |
| [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) | Velero + manual backup recipes + tested restore drill. |
| [docs/CLAUDE_DESKTOP.md](docs/CLAUDE_DESKTOP.md) | Wiring the MCP server into Claude Desktop. |
| [POSITIONING.md](POSITIONING.md) | Where Keos sits vs kagent, K8sGPT, Botkube, Cast AI. |
| [DEMO.md](DEMO.md) | Spoken-walkthrough scripts (A, B, C) for live demos. |
| [CHANGELOG.md](CHANGELOG.md) | Every release, every phase, backfilled from Phase 1. |

---

## Status

**v1.0.0-rc.1** — first release candidate; suitable for architect review and
internal pilots. See [release notes](docs/RELEASE_NOTES_1.0.0-rc.1.md) for
what's tested, what isn't, and known limitations.

**Not production-ready out of the box**: the dev `AUDIT_HMAC_KEY` in
`.env.example` is a placeholder; the SPA bearer token is shared rather than
per-operator. Both are tracked items in [SECURITY.md](SECURITY.md).

---

## Contributing & support

- **Found a bug?** Open an issue with the auto-loaded template under
  `.github/ISSUE_TEMPLATE/bug.md`.
- **Have a question / want a feature?** Use `support.md` /
  `feature.md` from the same templates.
- **Want to contribute code?** See [CONTRIBUTING.md](CONTRIBUTING.md)
  for the dev setup, style, and DCO sign-off requirement.
- **Security disclosure?** Do NOT open a public issue — follow
  [SECURITY.md](SECURITY.md).

Created by the Keos platform team.
