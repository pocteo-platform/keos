# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

_Nothing yet — bump the most recent in-flight change here as it lands._

---

## [1.0.0-rc.1] — 2026-06-01

First release candidate. Production hardening across the board; expected to
graduate to `1.0.0` after one internal pilot deployment.

### Added — Phase 10 (v1 readiness)

- **Production hardening** (10.1): pod security contexts (runAsNonRoot,
  readOnlyRootFS, seccomp RuntimeDefault, drop ALL capabilities) on api,
  mcp-server, frontend, stateful-stores. Liveness / readiness / startup
  probes wired in Helm. PodDisruptionBudget for api + mcp-server.
  Per-component NetworkPolicy gated on `networkPolicy.enabled` (default
  true). slowapi-backed global per-IP rate limit (120/min, exempt for
  probe + `/metrics` paths). CORS via `KEOS_ALLOWED_ORIGINS` env. Bearer
  token from K8s `Secret` projected volume via `SHP_API_TOKEN_FILE`.
- **Observability** (10.2): 12 named Prometheus metrics (graph runs,
  LLM latency, OPA decisions, escalations, approvals, observer events
  + errors, NATS queues, agent mode). `/api/metrics` + `/metrics`
  endpoints. Structured JSON logging via `python-json-logger`, controlled
  by `LOG_FORMAT` env. Optional `ServiceMonitor` CR gated on
  `monitoring.enabled`. `docs/OBSERVABILITY.md` with alerts + Loki LogQL
  recipes.
- **CI/CD pipeline** (10.3): GitHub Actions `ci.yml` (python-lint,
  python-typecheck, python-test matrix 3.11+3.12, frontend-lint,
  frontend-typecheck, frontend-build, helm-lint, helm-template-validate
  via kubeconform, opa-test, docker-build). `release.yml` on `v*` tags:
  multi-arch buildx push to GHCR, cosign keyless OIDC signing, syft SBOM
  attestations, helm chart pushed as OCI artifact, auto-generated release
  notes. Dependabot for pip, npm, docker, github-actions. Ruff + mypy
  configs. ESLint flat config (closes the gap where `npm run lint`
  previously crashed). Make targets: `lint`, `format`, `typecheck`,
  `coverage`.
- **Data lifecycle** (10.4): audit log daily rotation
  (`TimedRotatingFileHandler`, env-configurable `backupCount`). Background
  retention loop sweeps incident JSON + trace JSONL files older than
  configurable thresholds. `verify_audit_log()` walks rotated siblings —
  HMAC chain stays verifiable across rotation. Helm `retention.*` values
  + optional Velero `Schedule` CR gated on `backup.velero.enabled`.
  `docs/BACKUP_RESTORE.md` with Velero recipe, manual `kubectl cp`
  recipe, and a tested restore-drill walkthrough.
- **Reliability hardening** (10.5): Ollama calls retry 3× with 1/2/4 s
  backoff on 5xx + RequestError; raise `OllamaUnavailable` after
  exhaustion (caller routes to escalate). 4xx is non-retryable.
  `vector_search` retries 3× then returns `[]` (graceful degrade —
  graph continues with no RAG context, more likely to escalate
  defensively). OPA fail-closed property preserved + explicitly tested.
  SIGTERM / SIGINT handlers; `_drain_in_flight` waits up to 60 s for
  in-flight graph runs to ack before exit. 8 new hermetic reliability
  tests.
- **Governance + licensing** (10.6): CONTRIBUTING.md, SECURITY.md,
  CODEOWNERS, CHANGELOG.md, PR template, 3 issue templates, README
  badges.
- **Frontend polish** (10.7): Vitest + jest-dom + jsdom test harness;
  25 unit tests across `computePhaseStates`, `OutcomeBadge`, `branding`,
  `ws`. Prettier config + flat ESLint config with `no-explicit-any`
  promoted to error. Husky `pre-commit` hook running `lint-staged`
  (eslint --fix + prettier --write). 54 frontend files auto-reformatted.
- **GPU / Jetson runtime story** (10.8): `ollama.gpu.*` Helm value block
  (enabled, resourceName, count, runtimeClassName, tolerations,
  nodeSelector). Conditional GPU rendering in `stateful-stores.yaml`
  (NVIDIA Container Toolkit + RuntimeClass). `docs/JETSON.md` —
  end-to-end Orin Nano 8 GB walkthrough including k3s install, NVIDIA
  Container Toolkit, device plugin, perf expectations (4-5 min CPU →
  20-30 s on GPU), power-management notes. Cross-references from
  `docs/INSTALL.md`, `docs/CONFIG_REFERENCE.md`, `docs/INDEX.md`.
- **Operational runbooks** (10.9): 10 new runbooks under
  `docs/RUNBOOKS/` (agent-api-down, ollama-down, nats-down,
  qdrant-corruption, audit-tampering, runaway-graph, upgrading,
  disaster-recovery, incident-response-agent, secret-rotation) +
  decision-tree index README. Uniform 5-section template (Symptom,
  Triage 5 min, Containment, Resolution, Postmortem). Disaster-recovery
  defines a 30-minute RTO with three restore paths (Velero / manual
  tar / no-backup graceful degrade).
- **Release integration** (10.10): version bumps across
  `Chart.yaml`, `pyproject.toml`, `frontend/package.json`,
  `mcp_server/__init__.py`. `docs/RELEASE_NOTES_1.0.0-rc.1.md` with
  highlights, install matrix, known limitations. README status badge.

### Changed

- ruff format applied across the whole tree (54 files reformatted in
  10.3). No behavior change; surface-area cleanup.
- 75 lint findings auto-fixed (unused imports, deprecated
  `datetime.utcnow` → `datetime.now(UTC)`, ambiguous loop variables).

### Security

- Bearer-token comparison switched to `secrets.compare_digest` (was a
  plain `==`). Constant-time, no length-leak.
- Webhook endpoints (Alertmanager) use constant-time HMAC comparison.
- All new contributors must sign-off commits (DCO) — enforced in CI.

---

## [0.9.x] — Phase 9 (Pocteo → Keos brand pivot + autonomy)

### Added

- 9.0 Visual identity: Space Grotesk font (self-hosted), 0px border-radius,
  Pocteo (later Keos) color palette, footer attribution.
- 9.1 Generic terminology: "K8sGPT" / "Falco" replaced with neutral terms
  ("Cluster signals", "Telemetry event") in the UI.
- 9.2 Domain pivot: security framing dropped, full SRE focus. 7 SRE
  runbooks. Action catalog re-keyed: `restart_pod`, `rollout_undo`,
  `scale_deployment`, `cordon_node`, `uncordon_node`, `drain_node`,
  `page_human`. OPA matrix updated.
- 9.3 Chaos Mesh integration: `chaos_observer` watches kubelet events,
  translates to `infra.events.<kind>` NATS messages. Use Case wizard
  Step 3 accepts a ChaosExperiment kind.
- 9.4 Timeline view + Telemetry sidebar with Recharts.
- 9.5 Helm chart + multi-arch image build (`buildx-bake.hcl`). Talos
  notes.
- 9.6 Comprehensive user guide (15 sections), install matrix, Talos
  doc, config reference.
- Live-event detection across all non-system namespaces; Alertmanager
  webhook bridge (`POST /api/webhooks/alertmanager`).
- Stale-event short-circuit at triage (skips LLM cycle for events older
  than `STALE_EVENT_S` whose target is already Ready or gone).
- Slack + generic webhook delivery from `page_human`.
- Approvals queue with human-override flow, surfaced in the Control view.
- Use case Run flow + animated `RunningIndicator` with real-time WS-driven
  phase progression.
- Bulk-delete incidents UI; "Replay in pipeline" affordance from the
  incident detail page.

### Changed

- Two-lane NATS consumer (`pocteo-agent-user` for SPA-initiated runs,
  `pocteo-agent-obs` for kubelet events) so user clicks never wait
  behind observer backlog.
- JetStream `ack_wait` bumped to 600 s (graph runs on CPU LLM take
  ~5 min — default 30 s would have caused redelivery loops).
- `mode_store` watch thread made bulletproof (all-Exception catch +
  outer keep-alive loop + retry-with-backoff on initial GET).
- `chaos_observer` switched from `asyncio.get_event_loop()` (broken on
  Python 3.10+ in worker threads) to a captured-at-startup loop.

### Fixed

- Bind-mount file-vs-directory trap on first `docker compose up` before
  the kubeconfig file existed. `up` now seeds placeholder files first.
- Kubeconfig YAML corrupted by PS 5.1 `Set-Content -Encoding utf8` BOM +
  PS array-to-string `$OFS` join.
- Stale persisted state from earlier security-themed use-cases (still
  referencing `isolate_pod` etc.) handled defensively by `list_all` +
  one-shot migration.
- Sidebar drawer + responsive layout on viewports < md.

---

## [0.8.x] — Phase 8 (Web UI)

### Added

- 8.1 FastAPI API + WebSocket (`/ws/events`) with TraceBus pub/sub.
- 8.2 React 18 + Vite + TypeScript + Tailwind + shadcn/ui scaffold.
  Bilingual EN/FR via i18next.
- 8.3 Pipeline visualization (live + replay) with React Flow canvas +
  per-node drawer.
- 8.4 Incidents list + 8-tab detail page (Pipeline, Telemetry event,
  Runbook context, Diagnostic signals, LLM responses, Policy decision,
  Action result, Audit chain w/ HMAC verifier).
- 8.5 Use Case Creator (6-step wizard) with ResultsBanner.
- 8.6 Caddy reverse proxy, Playwright E2E suite, `demo-ui` Make target.

---

## [0.7.x] — Phase 7 (Positioning, K8sGPT, MCP)

### Added

- 7.1 `POSITIONING.md` — landscape vs kagent / K8sGPT / Botkube /
  Cast AI.
- 7.2 K8sGPT as diagnostic enricher via MCP. New `k8sgpt_analyze` graph
  node.
- 7.3 MCP server (`fastmcp`) exposing the 5 (later 6) action tools.
  The agent's own `execute` node calls its own MCP server over HTTP.

---

## [0.6.x] and earlier — Phases 1-6 (Core PoC)

### Added

- 1. Off-cluster stack (Ollama, Qdrant, NATS, OPA via docker-compose).
- 2. k3d cluster + RBAC + AgentMode CRD + demo workloads.
- 3. OPA policy (action × severity matrix) + 17 hermetic policy tests.
- 4. LangGraph state machine (8 nodes, Observe → Predict → Heal →
  Learn loop).
- 5. Synthetic event scenarios + `make demo` end-to-end CLI walkthrough.
- 6. Test suite (graph + policy) + README + DEMO.md + teardown.

---

[Unreleased]: https://github.com/your-org/self-healing-poc/compare/v1.0.0-rc.1...HEAD
[1.0.0-rc.1]: https://github.com/your-org/self-healing-poc/releases/tag/v1.0.0-rc.1
