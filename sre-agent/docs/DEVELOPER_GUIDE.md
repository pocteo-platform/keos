---
id: developer-guide
slug: /developer-guide
title: Developer Guide
sidebar_position: 6
description: Contribute to Keos — repo layout, local dev environment, the test suites, how the agent graph / MCP / OPA fit together for contributors, and the commit + release conventions.
---

# Developer Guide

How to build, test, and extend Keos. For the runtime design see
[Architecture](./architecture); for operating a deployment see the
[User Guide](./USER_GUIDE.md).

## Repository layout

```
sre-agent/
├── agent/                 # FastAPI app + the LangGraph agent
│   ├── graph.py           # the 9-node state machine
│   ├── llm.py             # diagnose() / plan_action() (Ollama, JSON-constrained)
│   ├── tools.py           # SRE action implementations
│   ├── security_tools.py  # security containment implementations
│   ├── policy_gate.py     # OPA client
│   ├── audit.py           # HMAC-signed audit chain
│   ├── mode_store.py      # AgentMode CRD watcher (kill-switch)
│   ├── drift/             # GitOps drift detection (SourceOfTruth providers)
│   ├── api.py             # REST + WebSocket + Falco webhook
│   └── runbooks/          # the RAG corpus (markdown)
├── mcp_server/            # MCP server exposing the action catalog
├── frontend/              # React + Vite SPA (TypeScript, Tailwind, i18n EN/FR)
├── deploy/helm/pocteo/    # the Helm chart (the canonical install)
├── deploy/gitops/         # Flux + SOPS manifests (Phase 23)
├── policies/agent.rego    # the OPA severity × action matrix
├── scripts/               # keosctl + install/release/build helpers
├── tests/                 # pytest (graph, policy, api, mcp, security)
└── docs/ + website/       # these docs + the Docusaurus site (renders ../docs)
```

## Local development

Python deps use **uv**; the frontend uses **npm**.

```bash
# Backend
uv sync --extra dev
uv run pytest                       # hermetic suites (mocks Ollama/OPA/NATS/Qdrant)
uv run ruff check . && uv run ruff format --check .
uv run mypy agent mcp_server scripts

# Frontend
cd frontend && npm ci
npm run lint && npm run typecheck && npm run build
npm test                            # vitest unit tests

# Docs site
make docs-dev                       # docusaurus start (renders ../docs)
make docs-build                     # CI=1 build (onBrokenLinks: throw)
```

The fastest inner loop against a real cluster is `keosctl install` on a local
k3d/kind, then `keosctl run <usecase>` and watch the Pipeline view.

## Testing

| Suite | Covers |
|---|---|
| `tests/test_graph.py` | the agent graph per scenario (mocked LLM/RAG) |
| `tests/test_policy.py` | the OPA matrix (runs against live OPA) |
| `tests/test_api.py` | REST/WebSocket endpoints + auth |
| `tests/test_mcp.py` | the MCP server tools + OPA defense-in-depth |
| `tests/test_security_domain.py` | security events + containment |

Graph tests are hermetic (`MCP_BYPASS=1` short-circuits the MCP hop). CI
(`.github/workflows/ci.yml`) runs lint, typecheck, the matrix on Python 3.11 +
3.12, helm lint + kubeconform render, opa check, the backup-restore drill, and a
no-push Docker build.

## Extending the agent

- **A new SRE/security action** — add the implementation to `agent/tools.py` or
  `security_tools.py`, expose it from `mcp_server/server.py`, add a row to the
  Rego matrix in `policies/agent.rego` (severity × required level), and a
  scenario to `tests/`. The graph picks it up via the tool catalog — no graph
  surgery.
- **A new runbook** — drop a markdown file in `agent/runbooks/`; `keosctl seed`
  re-embeds the corpus.
- **A new drift source** — implement the small `SourceOfTruth` protocol in
  `agent/drift/sources.py` (e.g. a Git/Flux provider); the comparator + detector
  are unchanged.
- **A chart knob** — add it to `deploy/helm/pocteo/values.yaml` with a comment;
  wire it in the relevant template; `keosctl values get` surfaces it.

## Documentation

The 6 canonical docs in `sre-agent/docs/` are the **single source of truth**.
The Docusaurus site (`sre-agent/website/`) renders them directly
(`docs.path: ../docs`) — do not author a second copy under `website/docs/`. Use
ASCII diagrams (Mermaid is disabled in the site for SSR stability). Keep stale
terms out: image names are `keos-*`, the registry path is `ghcr.io/pocteo-platform`
or your private `:5000`.

## Conventions

- **Commits** — Conventional Commits (`feat:`, `fix:`, `docs:`, `ci:`, scoped).
- **Branches** — feature branches off `main`; PR review before merge.
- **Style** — match the surrounding code; ruff + prettier enforce formatting.
- **Pre-PR** — `uv run pytest`, the lints/typechecks above, and `make docs-build`
  must pass.

See `CONTRIBUTING.md` and `SECURITY.md` at the repo root for the contribution
policy and the vulnerability-disclosure process.
