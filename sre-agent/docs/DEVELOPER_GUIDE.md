---
id: developer-guide
slug: /developer-guide
title: Developer Guide
sidebar_position: 7
description: Contribute to Keos — repo layout, local dev environment, the test suites, how the agent graph / MCP / OPA fit together for contributors, and the commit + release conventions.
---

# Developer Guide

How to build, test, and extend Keos. For the runtime design see
[Architecture](./ARCHITECTURE.md); for operating a deployment see the
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

The graph never changes — you extend Keos by adding to the **tool catalog**,
the **policy matrix**, the **runbook corpus**, or the **event sources**.

### Add a new action ("skill")

This is the common case — teach the agent a new remediation or containment.

1. **Implement it** in `agent/tools.py` (SRE) or `agent/security_tools.py`
   (security). A tool is a plain function that does its work via `kubectl`/the
   API and returns an `ActionResult` (`agent/schemas.py`) — `success`, captured
   `stdout`/`stderr`, an actionable `error`, and `meta` (e.g. `reversible`).
   Capture subprocess errors; never raise. Honour `dry_run` (return what it
   *would* do without mutating).
2. **Expose it on the MCP server** — add an `@mcp.tool()` wrapper in
   `mcp_server/server.py` that re-checks OPA, then calls your function. This is
   the execution boundary the agent (and Claude Desktop) goes through.
3. **Add a policy row** in `policies/agent.rego` — give the tool an autonomy
   level per severity in the `levels` object (L1/L2 = auto-allowed, L3/L4 =
   escalate). Lower for safe/reversible actions, higher for destructive ones.
   Add a `test_policy.py` case. `opa fmt` + `opa check` must pass.
4. **Let the planner choose it** — add the tool + its arg schema to the
   `plan_action` catalog in `agent/llm.py` so the LLM can propose it, and a
   diagnose/plan scenario in `tests/test_graph.py`.
5. **Document it** — add a row to the action catalog in
   [Architecture](./ARCHITECTURE.md#2-the-self-healing-loop-the-agent-graph)
   (and [Security](./SECURITY.md) if it's a containment tool).

The graph picks the tool up automatically via the catalog + the gate — no node
surgery.

### Edit the policy gate (OPA matrix)

`policies/agent.rego` is the single source of risk policy. The `levels` object
maps `action → {severity → L}`; `L ≥ 3` denies (→ escalate). Hard-deny gates
(protected namespaces, non-active mode, unknown action/severity) live below it.
To make an action more autonomous, lower its L at that severity; to gate it
harder, raise it. Test before shipping:

```bash
keosctl policy test <action> <severity> <ns> <mode>   # against the live OPA
uv run pytest tests/test_policy.py                     # the matrix unit tests
```

### Add a runbook (RAG knowledge)

Drop a markdown file in `agent/runbooks/` (symptom · diagnosis · remediation).
`keosctl seed` re-embeds the corpus into Qdrant; `diagnose` retrieves it by
domain. Keep it tight — one chunk per file.

### Add a use case (a triggerable scenario)

Author one in the SPA's **Use Cases → New** wizard, or add it to the library so
`keosctl run <slug>` fires it. A use case renders an event template (substituting
the real target pod), optionally seeds a runbook tagged to it, and asserts
expected outcomes — the basis of the demo + e2e suite.

### Add an event source / drift provider

Implement the small `SourceOfTruth` protocol in `agent/drift/sources.py` (e.g. a
Git/Flux/Argo provider) — the comparator + detector are unchanged. New telemetry
just needs to land on NATS `infra.events.*` / `security.events.*` in the event
shape `triage` expects.

### Add a chart knob

Add it to `deploy/helm/pocteo/values.yaml` with a comment, wire it into the
relevant template, and `keosctl values get/set` surfaces it. `helm lint` +
the kubeconform render in CI must pass.

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
