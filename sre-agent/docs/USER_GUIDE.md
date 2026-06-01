# Keos SRE Agent — User Guide

> **The autonomous SRE that ships inside every Keos edge node — by Pocteo Platform.**

**Pocteo Platform** is the company. **Keos** is Pocteo's Kubernetes edge OS.
The **Keos SRE Agent** (covered by this guide) is the autonomous
reliability layer that runs inside every Keos edge node — it watches the
local cluster, diagnoses infrastructure incidents with retrieval-augmented
context, plans a single remediation action, gates that action through Open
Policy Agent (OPA), executes it under scoped RBAC, verifies the cluster
healed, and writes an HMAC-signed audit record. The agent runs the loop
**Observe → Predict → Heal → Learn** end-to-end on every event.

This guide takes a reader from "what is this" to "I'm running the SRE Agent
on my edge cluster" without context-switching. Sections in dependency
order.

## Contents

1. [What Keos is (and isn't)](#1-what-pocteo-is-and-isnt)
2. [Architecture](#2-architecture)
3. [Install paths](#3-install-paths)
4. [Configuration reference](#4-configuration-reference)
5. [The agent's loop](#5-the-agents-loop)
6. [Mode CRD + safety model](#6-mode-crd--safety-model)
7. [Action catalog](#7-action-catalog)
8. [Use Case Creator walkthrough](#8-use-case-creator-walkthrough)
9. [Day-2 operations](#9-day-2-operations)
10. [Troubleshooting](#10-troubleshooting)
11. [Security model](#11-security-model)
12. [Audit log + HMAC verification](#12-audit-log--hmac-verification)
13. [Integrating with existing observability](#13-integrating-with-existing-observability)
14. [API reference](#14-api-reference)
15. [FAQ](#15-faq)

---

## 1. What this is (and isn't)

**Pocteo Platform** is the company. **Keos** is Pocteo's Kubernetes edge
OS. The **Keos SRE Agent** (this codebase) is the autonomous reliability
layer that ships inside every Keos edge node.

**The Keos SRE Agent is** an autonomous SRE for Kubernetes that takes
operational incidents from telemetry → diagnosis → remediation → verify,
without an on-call human in the loop for the majority of known-known
failures. It targets infrastructure issues: memory leaks, crashloops,
ImagePullBackOff, node pressure, bad rollouts, config drift, traffic
surges.

**The loop**:

| Phase | What happens |
|---|---|
| **Observe** | Cluster events (kubelet, Chaos Mesh, GitOps drift detectors) flow through a debounced bus into the agent. Retrieval-augmented context pulls relevant runbooks. Structured cluster signals describe the current state. |
| **Predict** | A local LLM (Qwen2.5-3B by default, CPU-friendly) reads telemetry + runbooks + signals and produces a JSON diagnosis with confidence + evidence. A second LLM call picks one of 6 remediation tools and concrete arguments. |
| **Heal** | The proposed action goes through OPA's severity-× action matrix. If allowed, it dispatches through the MCP server (same surface Claude Desktop or kagent would use) to kubectl. |
| **Learn** | The verify node polls the cluster until the action's effect is observable. Every state transition writes one HMAC-signed line to the audit log. The result feeds back into the operator's view (or the next training pass). |

**The Keos SRE Agent is NOT**:
- A replacement for SREs. It accelerates known-knowns; humans own the unknowns.
- A magic self-healing button. The action catalogue is intentionally narrow (6 verbs).
- A chat-for-kubectl. No natural-language query interface — telemetry in, actions out.
- A SIEM. The codebase pivoted away from security incidents in Phase 9; that's not its target.
- A cost-optimisation agent (that's Cast AI's role).
- A diagnostic translator alone (that's K8sGPT's role).

See [POSITIONING.md](../POSITIONING.md) for the full landscape table.

---

## 2. Architecture

```
┌─────────────────────────────── BROWSER ──────────────────────────────┐
│  Keos SPA (React + shadcn/ui + i18n EN/FR + 0-radius brutalist)    │
│  Cluster · Incidents · Pipeline · Use Cases · Control                │
└──────────────────────┬───────────────────────────────────────────────┘
                       │ REST + WebSocket
                       ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Caddy (reverse proxy on :80)                                        │
└──────┬───────────────────────────┬───────────────────────────────────┘
       │ /api · /ws                │ /
       ▼                           ▼
┌──────────────────────┐    ┌────────────────────────┐
│  FastAPI + Agent     │    │  nginx (built SPA)     │
│  - LangGraph state   │    └────────────────────────┘
│    machine           │
│  - NATS consumer     │
│  - Chaos observer    │
│  - Mode CRD watcher  │
└─┬──────┬──────┬──────┘
  │      │      │
  ▼      ▼      ▼
┌────┐ ┌────┐ ┌────┐
│OPA │ │MCP │ │NATS│
└────┘ └─┬──┘ └────┘
         │
         ▼
       kubectl → Kubernetes API
                  ↓
               (Chaos Mesh, kubelet, etc.)
       ↑
       └─ same OPA matrix gates Claude Desktop / kagent / Cursor via MCP

Vector RAG:  Qdrant ← Ollama nomic-embed-text
LLM:         Ollama Qwen2.5-3B (q4_K_M, ~1.9 GB)
Audit:       agent/audit.log (HMAC-SHA256, append-only)
```

All components run in containers. The agent has no kernel dependencies
(no eBPF, no Falco daemon set), so it ports cleanly to Talos, k3s on
Jetson, GKE, EKS, anywhere conformant.

---

## 3. Install paths

See [INSTALL.md](INSTALL.md) for the full matrix. Quick summary:

| Where | Command |
|---|---|
| **Laptop (Docker Desktop)** | `make demo-ui` — builds + runs `docker compose` stack + k3d cluster |
| **Generic K8s** | `make install-cluster KUBECONFIG=...` — Helm chart |
| **k3s on Jetson / RPi** | Same Helm command; multi-arch images pulled automatically |
| **Talos** | Helm + chaos-mesh runtime override; see [TALOS.md](TALOS.md) |
| **Air-gapped** | Pre-pull via `talosctl image cache`; see [INSTALL.md](INSTALL.md#air-gapped) |

The Helm chart is the canonical install. The docker-compose path is for
laptop iteration only.

---

## 4. Configuration reference

See [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) for the exhaustive table.
Auto-generated from `agent/config.py` and `deploy/helm/pocteo/values.yaml`
so it can't drift from the source.

The most important knobs:

| Setting | Default | Effect |
|---|---|---|
| `agent.defaultMode` | `observe-only` | Initial AgentMode CR. Use `observe-only` on first install. |
| `agent.confidenceThreshold` | `0.6` | LLM confidence below this routes the incident to escalate without ever calling plan_action. |
| `auth.apiToken` | _required_ | Bearer token the SPA + any direct API caller must present. Generate with `openssl rand -base64 32`. |
| `auth.hmacKey` | _required_ | HMAC-SHA256 key signing every audit-log line. Generate the same way. Rotation procedure in [Day-2 ops](#9-day-2-operations). |
| `chaos-mesh.enabled` | `true` | Helm sub-chart toggle. Set to `false` for clusters where chaos is out of scope. |

---

## 5. The agent's loop

The 9 internal LangGraph nodes map to 4 user-facing phases. The UI
renders this mapping on the timeline view (`/pipeline/live`).

| Phase | Nodes | Side-effect |
|---|---|---|
| **Observe** | `triage`, `retrieve_context`, `k8sgpt_analyze` | Read-only. Pulls runbook chunks from Qdrant and structured cluster signals from the diagnostic layer. |
| **Predict** | `diagnose`, `plan_action` | LLM calls. Validated by Pydantic; one retry on parse failure, then escalates. |
| **Heal** | `policy_gate`, `execute` | OPA gate first. If allowed, dispatch via MCP. Audit lines for every transition. |
| **Learn** | `verify`, `escalate` | Poll the cluster for the effect or escalate to a human. |

The agent **fails closed**: any uncaught error, low confidence, OPA deny,
execute failure, or unhealed verify routes the incident to `escalate` and
notifies a human. The audit log records both the failure and the
escalation.

---

## 6. Mode CRD + safety model

The `AgentMode` custom resource is the global kill-switch. Three values:

| Mode | Behaviour |
|---|---|
| `observe-only` | Graph runs end-to-end but `policy_gate` denies every action on mode grounds alone. Diagnosis still produced; nothing modifies the cluster. Default on first install. |
| `active` | Policy gate may allow. The action actually executes via MCP. |
| `frozen` | Same as observe-only for action gating, plus pending approvals are blocked from execution. Use during an active incident when even "approved" remediations should pause. |

Flip with `kubectl`:

```bash
kubectl patch agentmode default --type=merge -p '{"spec":{"mode":"active"}}'
```

The agent watches the CR; changes propagate in under 1 second.

---

## 7. Action catalog

Six remediation tools. Every action call is gated by OPA's matrix:

| Action | Args | Reversible | OPA matrix entry (severity → max level) |
|---|---|---|---|
| `restart_pod` | `pod`, `namespace` | yes (controller respawns) | low:L1 / medium:L1 / high:L2 / critical:L2 |
| `rollout_undo` | `deployment`, `namespace` | yes (`kubectl rollout undo`) | low:L3 / medium:L2 / high:L1 / critical:L1 |
| `scale_deployment` | `deployment`, `namespace`, `replicas` | yes (HPA can re-tune) | low:L2 / medium:L1 / high:L1 / critical:L1 |
| `cordon_node` | `node_name` | yes (`kubectl uncordon`) | low:L3 / medium:L2 / high:L1 / critical:L1 |
| `drain_node` | `node_name`, `grace_period_s` | yes | low:L4 / medium:L3 / high:L2 / critical:L1 |
| `page_human` | `reason` | n/a (logged + future Slack hook) | any severity / always allowed in active mode |

L1/L2 = allow, L3+ = deny.

Hard-deny rules sit ON TOP of the matrix:
- target namespace ∈ {kube-system, kube-public} → deny
- agent mode ≠ active → deny
- unknown action → deny
- unknown severity → deny

---

## 8. Use Case Creator walkthrough

The Use Case Creator (`/usecases`) lets operators define custom scenarios
that combine a Chaos Mesh experiment, a synthetic telemetry event, and
expected outcomes. Three library use cases ship pre-seeded:

- **Memory leak / OOM trajectory** — StressChaos memory, expects `rollout_undo`
- **Pod in CrashLoopBackOff** — `BackOff` event, expects `rollout_undo`
- **Node memory pressure** — `NodeNotReady` event, expects `cordon_node`

The 6-step wizard:

1. **Context** — name, EN+FR descriptions, free-form tags
2. **Target** — namespace, pod (specific OR label selector), owner kind
3. **Telemetry / Chaos** — Chaos Mesh experiment kind + rule + priority + output template with `{pod}/{ns}/{image}` placeholders
4. **Expected** — diagnosis keywords (substring match), proposed tool, policy decision, outcome
5. **Optional runbook** — EN + FR markdown; embedded into Qdrant tagged with the use-case id
6. **Review** — full preview + Save / Save & run

Save & run navigates to `/pipeline/<incident_id>?usecase=<id>` with a
green/red expectations banner above the canvas.

---

## 9. Day-2 operations

### Adding a runbook

Drop a markdown file in `agent/runbooks/` with sections `## Symptom /
## Diagnosis / ## Remediation`. Re-seed Qdrant:

```bash
python -m agent.seed_rag
```

The agent picks up the new runbook on the next event.

### Rotating the HMAC key

1. Generate a new key: `openssl rand -base64 32`
2. Update the Helm release: `helm upgrade pocteo deploy/helm/pocteo --set-string auth.hmacKey=<new>`
3. **Existing audit lines remain valid under the OLD key.** The agent
   restarts and signs new lines with the new key. Run `verify_audit_log`
   against both keys to validate across the rotation boundary.

### Rotating the bearer token

```bash
helm upgrade pocteo deploy/helm/pocteo --set-string auth.apiToken=$(openssl rand -base64 32)
```

The api Pod restarts; users will be prompted to re-paste on next request.

### Upgrading the model

Edit `values.yaml` → `ollama.chatModel`. On `helm upgrade`, the Ollama
StatefulSet re-pulls. Run `python -m agent.pull_models` to pre-warm.

### Upgrading the Helm chart

```bash
git pull
helm dependency update deploy/helm/pocteo
helm upgrade pocteo deploy/helm/pocteo -n pocteo
```

### Backup + restore

PVCs to back up:
- `ollama` — model weights (~2 GB; re-pullable)
- `qdrant` — RAG vectors (small; re-seedable via `make seed`)
- `nats` — JetStream (event durable cache; can be lossy in restore)

The audit log lives on the api Pod's `emptyDir` by default — switch to a
PVC for retention. See [INSTALL.md](INSTALL.md) for the values override.

---

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| SPA shows "No token" badge | `.env.local` missing | `make up` regenerates it |
| `/api/health` returns 200 but mode is null | Cluster unreachable from api container | Check `agent.mode_store` logs; verify kubeconfig mount |
| Live pipeline never animates | NATS consumer not subscribed | `kubectl logs deploy/api | grep "subscribed to infra.events"` |
| All actions denied as "agent_mode" | Agent in observe-only | `kubectl patch agentmode default --type=merge -p '{"spec":{"mode":"active"}}'` |
| `make demo-ui` hangs on first run | Ollama pulling 2 GB of model weights | Wait 5–10 min; subsequent runs skip the pull |
| K8sGPT findings empty | k8sgpt URL unreachable | Confirm `K8SGPT_URL` env on api Deployment |
| Chaos Mesh pods CrashLoop | Wrong containerd socket | Set `chaos-mesh.chaosDaemon.socketPath` per your runtime (see TALOS.md) |
| HMAC chip red on audit row | Audit file tampered OR HMAC key rotated | Run `verify_audit_log` against both old and new key |

---

## 11. Security model

Two boundaries:

**Trust boundary 1: bearer token at the API.** All non-health endpoints
require `Authorization: Bearer <token>`. The SPA stores the token in
localStorage. WebSocket uses the same token as a query param. The token
is minted by `make up` and lives in `.env.local` (gitignored).

**Trust boundary 2: OPA + RBAC at the action.** Every action — agent or
operator-driven (Claude Desktop / Cursor / kagent) — flows through the
MCP server, which calls OPA. OPA's matrix is the SOLE source of action
permission. RBAC on the cluster is the second line of defence:

- Agent ServiceAccount can do `pods [get, list, watch, delete, patch]`,
  `pods/eviction [create]`, `deployments [...]`, `deployments/scale [...]`,
  `nodes [patch, update]` (cluster-scoped). NOTHING in kube-system /
  kube-public regardless of mode.

**Threat model summary**:

| Attacker | Can they... | Why not |
|---|---|---|
| Read access to NATS | Trigger a fake event | They'd need API auth to act on it; agent still gates via OPA |
| Token theft | Talk to the API | True — rotate; treat token as a credential |
| Cluster-admin attacker | Bypass the agent | Yes; that's by definition. The agent doesn't promise to defend against attackers WITH cluster-admin already |
| Audit log tampering | Hide an action | No — HMAC chain reveals tamper. Rotate the key + investigate |

---

## 12. Audit log + HMAC verification

Every state transition writes one JSON-lines record to
`agent/audit.log`. Each record carries an HMAC-SHA256 signed over the
canonical (sort-keys) form of the record without the signature. Verify
the full file offline:

```python
from agent.audit import verify_audit_log
from agent.config import settings
n, invalid = verify_audit_log(settings.audit_log_path, settings.audit_hmac_key)
print(f"{n} lines verified; {len(invalid)} invalid")
```

The SPA's Audit Chain tab (`/incidents/<id>` → Audit chain) shows per-
record validity inline. Operator-driven actions (Claude Desktop /
kagent via MCP) carry `source: "mcp"`; agent-driven actions carry
`source: "agent"`.

---

## 13. Integrating with existing observability

- **Prometheus**: the api Pod exposes `/metrics` (Phase 10 roadmap).
  Scrape via PodMonitor / ServiceMonitor.
- **Loki / SIEM**: the audit log is JSON-lines on disk. Mount as a
  PVC and ship via Promtail / Fluent Bit. Each record carries an
  `event_type` + `incident_id` for indexing.
- **Slack escalation**: today `page_human` writes a structured log line.
  Replace with a Slack webhook in `agent/tools.py:page_human` for a
  Botkube-style integration.
- **GitOps**: the OPA `agent.rego` policy is mounted from a ConfigMap
  rendered by the chart. Manage in your Git source of truth like any
  other policy.

---

## 14. API reference

Auto-generated from FastAPI's OpenAPI at `/api/docs` (Swagger UI) or
`/api/openapi.json`. Endpoint groups:

- `GET /api/health` — open, returns `{ok, agent_mode, version}`
- `GET|POST /api/mode` — read / patch AgentMode CR
- `GET /api/cluster/{nodes,namespaces,pods,deployments,events}` — read cluster state
- `GET /api/incidents`, `/api/incidents/{id}`, `/api/incidents/active` — incident store
- `GET /api/audit`, `POST /api/audit/verify` — audit log
- `GET /api/scenarios`, `POST /api/scenarios/run` — built-in scenarios
- `GET|POST|PUT|DELETE /api/usecases` + `POST /api/usecases/{id}/run` — user-defined scenarios
- `GET /api/approvals`, `POST /api/actions/{approve,deny}` — pending L3 approvals
- `WS /ws/events?token=...` — multiplexed pipeline / falco / cluster / mode / audit

All non-health endpoints require `Authorization: Bearer <token>`.

---

## 15. FAQ

**Q: Why a 3B model? Won't a bigger model give better diagnoses?**
A 3B q4 model fits in 2 GB RAM and runs on CPU at ~25–40 tokens/sec on a
Jetson Orin Nano. The bottleneck for known-knowns is the runbook + cluster
signals, not the model size. Swap to Llama 3.1 8B or Qwen 2.5 7B for
clusters that can spare the GPU.

**Q: Why not LangChain's prebuilt SRE agents?**
Closed catalogues = unbounded blast radius. Keos's 6-action surface +
explicit LangGraph state machine + OPA matrix lets you reason about every
possible decision. LangChain's tool-binding agents typically can't.

**Q: How do I add a new action?**
1. `agent/tools.py` — implement the kubectl call.
2. `mcp_server/server.py` — add a new `@mcp.tool()`.
3. `policies/agent.rego` — extend the `levels` object.
4. `agent/llm.py` — add to the planner's catalog example.
5. `tests/test_policy.py` — add cases.
6. `tests/test_mcp.py` — add to the tool list assertion.

**Q: Can Keos run multi-cluster?**
Today no — one agent per cluster. Multi-cluster is a Phase 10 candidate
(fleet view + per-cluster agent + shared incidents store).

**Q: How do I review pending approvals?**
`/control` view → Pending approvals section. Or `GET /api/approvals` for
the JSON.

**Q: Is the audit log enough for SOC 2?**
It's a strong foundation: signed, append-only, full action provenance.
You'll still need policies, retention rules, and an external SIEM. The
log format is intentionally machine-readable for SIEM ingestion.

---

Created by Keos platform.
