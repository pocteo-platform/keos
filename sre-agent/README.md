<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img src="website/static/img/keos-shape.jpg" alt="Keos" width="150">
</p>

<h1 align="center">Keos SRE Agent</h1>

<p align="center">
  <em>The autonomous SRE + security agent that lives inside every edge Kubernetes node.</em>
</p>

<p align="center">
  <a href="https://github.com/pocteo-platform/keos/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/pocteo-platform/keos/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://pocteo-platform.github.io/keos/"><img alt="Docs" src="https://img.shields.io/badge/docs-pocteo--platform.github.io%2Fkeos-15117A"></a>
  <img alt="Images" src="https://img.shields.io/badge/images-linux%2Famd64%20%7C%20linux%2Farm64-informational">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue">
</p>

---

Keos observes an edge cluster, **diagnoses** incidents with a local LLM + retrieval,
**remediates** through a hard policy gate, **verifies** the fix, and **signs** every
action — all in-cluster, with no cloud round-trip. It covers two domains over one
state machine: **SRE self-healing** (crashloops, OOM, node pressure, bad rollouts,
config drift) and **runtime security** (reverse shells, miners, priv-esc, exfil via
Falco/Tetragon). It runs on CPU and is GPU-accelerated, ARM64-native, and air-gap ready.

**Pocteo Platform** is the company; **Keos** is its Kubernetes edge OS; this repo is
the agent that runs on it (and on any conformant cluster).

## What it does

- **Closed-form agent loop** — a 9-node LangGraph state machine (triage → retrieve →
  diagnose → plan → policy-gate → execute → verify → escalate), not a free-form ReAct loop.
- **Hard safety mesh** — an OPA severity×action matrix, scoped RBAC, a hot-flippable
  kill-switch (`observe-only`/`active`/`frozen`), and an HMAC-signed, tamper-evident audit chain.
- **Local & private** — Ollama inference + Qdrant RAG on the node; nothing phones home.
- **Reversible actions** — every containment can be undone (`keosctl quarantine release`).
- **Edge delivery** — one Helm chart; multi-arch images; private-registry and **pull-based
  GitOps (Flux + SOPS)** for fully air-gapped fleets.

See **[Architecture](docs/ARCHITECTURE.md)** for the full design.

## Quick start

```bash
git clone https://github.com/pocteo-platform/keos && cd keos/sre-agent
keosctl install          # interactive: cluster · model · images · mode · security
keosctl ui               # open the web UI (paste the printed token)

keosctl mode active
keosctl run lib-reverse-shell    # watch the agent contain a live threat
keosctl events -w                # stream incidents
```

`keosctl` is the single entry point for everything — install, day-2 ops, release,
GitOps, GPU, audit. Full list: **[CLI Reference](docs/CLI_REFERENCE.md)**.

## Where it runs

| Target | Notes |
|---|---|
| **k3s / kubeadm / generic K8s** (amd64 + arm64) | the common case; `keosctl install` |
| **Talos / Kairos** (immutable edge OS) | recommended for fleets; managed from a workstation |
| **NVIDIA Jetson (Orin)** | GPU inference (the loop runs in seconds) |
| **Private registry / air-gapped** | no public pulls; optional Flux GitOps pull-delivery |

Step-by-step for each: **[Installation](docs/INSTALLATION.md)**.

## Documentation

| Guide | For |
|---|---|
| [User & Operator Guide](docs/USER_GUIDE.md) | running it day to day |
| [Architecture](docs/ARCHITECTURE.md) | how it works |
| [Installation](docs/INSTALLATION.md) | every distro / arch / GPU / air-gap |
| [Security Domain](docs/SECURITY.md) | Falco/Tetragon, detection → containment |
| [CLI Reference](docs/CLI_REFERENCE.md) | every `keosctl` command |
| [Operations & Release](docs/OPERATIONS.md) | registry, GitOps, GPU, backup, release |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | repo layout, tests, extending the agent |
| [Runbooks](docs/runbooks/README.md) | incident-response playbooks |

Hosted docs: **<https://pocteo-platform.github.io/keos/>**

## Built on

[LangGraph](https://github.com/langchain-ai/langgraph) ·
[Ollama](https://ollama.com) ·
[Qdrant](https://qdrant.tech) ·
[Open Policy Agent](https://www.openpolicyagent.org) ·
[K8sGPT](https://k8sgpt.ai) ·
[Model Context Protocol](https://modelcontextprotocol.io) ·
[Falco](https://falco.org) ·
[Tetragon](https://tetragon.io) ·
[Flux](https://fluxcd.io) ·
[Chaos Mesh](https://chaos-mesh.org)

## Contributing & policy

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) ·
[CHANGELOG.md](CHANGELOG.md) · Apache-2.0

<p align="center"><sub>By <a href="https://github.com/pocteo-platform">Pocteo Platform</a> · Apache-2.0</sub></p>
