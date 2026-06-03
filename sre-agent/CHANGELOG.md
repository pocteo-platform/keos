# Changelog

All notable changes are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — V1 UI polish (Phases 14.5 through 14.10)

- **Phase 14.5 — Komodor-inspired UI uplift**: workload drill-down route
  `/workload/:ns/:kind/:name` with WorkloadHeaderCard, related-incidents
  table, and forecast horizons panel. Unified pipeline view
  (`UnifiedPipelineGrid`) replaces the dual React-Flow / timeline split.
  New `/api/scene/:incidentId` aggregates everything the pipeline scene
  needs in one round-trip. Workload page polling cadences tuned per
  data source.
- **Phase 14.6 — V1 UI completion**: dedicated Reliability page (`/reliability`)
  with workload + infrastructure health tiles, severity-bucketed risks,
  30-day issues-over-time chart. Dedicated Optimization page (`/optimization`)
  with rightsizing recommendations derived from baselines. Unified Settings
  page (`/settings`) with 7 tabs replacing the old `/control` and
  `/calibration` top-level routes. Complete EN/FR i18n for every visible
  string. Operator-flow nav ordering: Dashboard → Reliability → Cluster →
  Incidents → Pipeline → Use Cases → Optimization → Settings.
- **Phase 14.7 — V1 polish**: Settings tabs now show LIVE data alongside
  env-var knobs (Forecasting tab with `/api/forecasts` table + level
  chips; Baselines tab with `/api/baselines` table + σ-graded tones;
  new Drift tab for Phase 15 events). Dashboard gains a
  `LiveActivityRibbon` streaming the last 12 audit events with
  per-stage icons and Framer-Motion entrance. AuditTable gets full
  pagination (50/page) + column sort + payload-JSON search. Reliability
  page adds a GitOps-drift tile counting drift incidents over 7d.
- **Phase 14.8 — pagination everywhere**: Optimization recommendations
  paginated (25/page) with sortable columns + direction filter +
  search. Cluster Events list paginated. Settings → Calibration
  sortable on every numeric column.
- **Phase 14.9 — Use Cases search + sidebar live counter**: Use Cases
  page gains search input + tag-chip filter + match-count badge +
  no-match empty state. Sidebar shows live active-incidents pill on
  the Incidents nav item (4s poll, pulsing warn dot).
- **Phase 14.10 — Pro sidebar + topbar**: sidebar is collapsible (64px
  ↔ 240px) with persistent state and `[` keyboard shortcut.
  Sectioned nav (OVERVIEW / OPERATE / LEARN). Persistent
  agent-mode pill above the footer (live-polled). Smooth 200ms
  transitions. Topbar gets a URL-driven breadcrumb, an API + WS
  health combo pill (via new `useWsStatus()` hook reading
  `WsClient.onStatus`), and a clickable mode badge linking to
  Settings → Agent. Custom theme-aware scrollbars across the entire
  app (no native browser chrome ever shown).

### Added — Phase 15 (GitOps drift detection)

- **Drift package** (`agent/drift/`): `SourceOfTruth` protocol +
  `ManifestsSource` filesystem provider (walks a YAML directory, skips
  `Event` / `Lease` / `Pod` kinds by default, picks up per-manifest
  `pocteo.io/drift-ignore-fields` annotations). Pure-function deep-diff
  comparator with a comprehensive whitelist for server-controlled
  (`metadata.uid`, `resourceVersion`, `managedFields`, `status.*`) and
  controller-owned (`spec.replicas`, `spec.clusterIP`, nodePort)
  fields. Background detector polls every `DRIFT_TICK_INTERVAL_S`
  (default 120 s), publishes `infra.events.drift.<kind>` on divergence,
  with a 600 s dedup window per (kind, ns, name).
- **`revert_drift` action tool** (`agent/tools.py`, `mcp_server`):
  reads the SoT manifest from `sot_source` (path or `path#doc_index`),
  defensively refuses to apply if the doc's kind/name don't match the
  request, then `kubectl apply -f`'s the single doc. Wired through OPA
  matrix (`L3 low / L2 medium / L1 high+critical`), schemas literal,
  graph dispatcher, llm catalog hints, MCP server's pre-execute gate.
- **Runbook 012** (`agent/runbooks/012-drift.md`): symptoms (operator
  edits / controller writeback / stale SoT), diagnosis cross-references
  (managedFields + severity), preferred remediation paths, and the
  three operator escape hatches (per-manifest ignore annotation, global
  whitelist override, `DRIFT_SOURCE_TYPE=none` kill switch).
- **Compose + Helm wiring**: `manifests/` bind-mounted into the api
  container at `/manifests` (read-only); `DRIFT_SOURCE_TYPE=manifests`
  + `DRIFT_SOURCE_PATH=/manifests` set in `docker-compose.yml`.
- **Prometheus metric**: `drift_events_total{kind}`.
- **Tests**: 32 new hermetic tests in `tests/test_drift.py` covering
  strip semantics, comparator edge cases (whitelist, extra ignore
  paths, added/removed/modified deltas), ManifestsSource (multi-doc
  files, unparseable YAML, missing root, ignore annotation), detector
  (publishes on drift, no-op in sync, missing live, dedup cooldown,
  soft-fail per target, NATS down), and the `revert_drift` tool itself
  (empty/missing/invalid sot_source, defensive kind/name mismatch
  guard). 3 new OPA matrix cases for `revert_drift`. MCP tool-count
  test bumped to 11 tools with `sot_source` schema check.
- **Documentation**: New `website/docs/architecture/gitops-drift.md`
  page; updated `website/docs/architecture/overview.md` to reference
  the drift event class + 4-layer continuous-intelligence loop; new
  ARCHITECTURE.md §8 covering Phases 12-15.

### Added — Phase 14 (predictive forecasting + new remediation tools)

- **Forecasting package** (`agent/forecasting/`): pure-Python OLS
  linear-regression forecaster over 60-sample windows from the Phase
  13 baseline store. Three horizons (5/15/60 min). `R²` floor discards
  non-linear (seasonal / autoscaling-feedback) fits. Background
  scheduler tick (60 s) publishes `infra.events.forecasted_<kind>`
  events when any fitted baseline crosses its danger threshold within
  a horizon.
- **Severity step-down**: forecasted events fire at one severity step
  LOWER than the reactive equivalent so OPA's matrix is more
  conservative for autonomous responses.
- **Three new action tools** in `agent/tools.py` + `mcp_server`:
  - `tune_hpa(deployment, min, max, target_cpu)`: raise HPA caps ahead
    of a forecast traffic surge.
  - `set_resources(deployment, requests, limits)`: rightsizing in
    response to forecast memory growth.
  - `evict_workload(namespace, label_selector)`: noisy-neighbour
    eviction before it OOMs siblings.
- **OPA matrix** extended with the three new actions (HPA tune at high
  severity = L1; low severity = L3 to require operator approval).
  `tests/test_policy.py` covers the new matrix rows.
- **Three new runbooks** (`009-traffic-forecast.md`,
  `010-node-decay-prediction.md`, `011-rightsizing.md`) seeded into
  Qdrant by `make seed`.
- **Settings → Forecasting tab** in the SPA (toggle scheduler tick,
  inspect last-forecast state per fitted key).

### Added — Phase 13 (ML baselines for cluster metrics)

- **Baselines package** (`agent/baselines/`): Prometheus scraper (60 s
  tick, 600 s fit cadence), EWMA mean + per-hour-of-day median,
  per-(metric, namespace, pod) keys persisted to `data/baselines.db`.
  Dual transport (compose-side `http://prometheus:9090` OR in-cluster
  `k8s-proxy://monitoring/prometheus:9090` via apiserver proxy, the
  Phase 13c default).
- **`enrich_with_baselines` graph node** inserted between
  `k8sgpt_analyze` and `diagnose`; injects "Memory is 838 MiB, baseline
  412 ± 18 MiB → 23 σ above (not seasonal)" context into the diagnose
  prompt for every fitted key on the targeted pod.
- **Cold-start handling**: below 50 samples per key, the prompt notes
  "still learning" and the LLM weights raw values as before.
- **Internal API**: `GET /api/baselines/<metric>?ns=&pod=` returns
  `{baseline, current, sigma_above, seasonal_residual}`.
- **Helm + compose**: Prometheus + kube-state-metrics added to the
  stack (`k3d/bootstrap.sh` installs both in `ns/monitoring`).

### Added — Phase 12 (confidence telemetry + per-rule calibration)

- **Calibration package** (`agent/calibration/`): SQLite store at
  `data/calibration.db` schema `(rule_id, n_incidents, n_correct,
  p50_conf, p90_conf, threshold)`. Grader updates per-rule stats after
  every `verify` outcome; `_route_after_diagnose` reads per-rule
  threshold (fallback: global `AGENT_CONFIDENCE_THRESHOLD`).
- **Prometheus histograms**: `keos_diagnose_confidence{rule}` and
  `keos_diagnose_accuracy{rule}`.
- **Calibration view**: Settings → Calibration tab in the SPA with
  per-rule table, sparkline, and manual threshold-override input
  (`POST /api/calibration/<rule>/threshold`).
- **Doctor**: new line
  `[OK]  CALIBRATION  median_conf=0.71 below_90pct=14/50 rules_calibrated=8/8`.

### Fixed — Reliability page i18n

- Pluralized "across N namespace(s)" hint via i18next `_one`/`_other`
  keys (`unhealthyHint_one`, `unhealthyHint_other`) in both EN and FR
  locales; updated `reliability.tsx` to pass `count` so the right form
  resolves.

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
