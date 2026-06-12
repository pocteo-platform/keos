---
id: architecture
slug: /architecture
title: Architecture
sidebar_position: 2
description: How Keos works end to end — the self-healing loop, the safety mesh (OPA + RBAC + AgentMode + signed audit), the data planes, the security domain, and the deployment shape for edge clusters.
---

# Architecture

Keos is an **autonomous SRE + security agent that lives inside the edge
Kubernetes cluster it protects**. It observes the cluster, diagnoses incidents
with a local LLM + retrieval, proposes a remediation, runs that remediation
through a policy gate, executes it under scoped RBAC, verifies the outcome, and
writes a tamper-evident audit record — all without phoning home.

This page is the system map. For *operating* it see the
[User & Operator Guide](./USER_GUIDE.md); for *installing* it see
[Installation](./INSTALLATION.md); for *building & releasing* it see
[Operations](./OPERATIONS.md).

## 1. The big picture

```
            ┌──────────────────────────── edge cluster ───────────────────────────┐
 telemetry  │  kubelet events ──┐                                                  │
 sources    │  Falco / Tetragon ─┼─► NATS JetStream ─► ┌─────────────────────────┐ │
            │  Chaos Mesh ───────┘   (durable bus)     │   agent (LangGraph)     │ │
            │                                           │  triage→…→verify→audit  │ │
 operator   │  keosctl / SPA ─► FastAPI ─► MCP server ──┤  diagnose: Ollama + RAG │ │
            │                       │           │       └────────┬────────────────┘ │
            │                       │           ▼ OPA gate        ▼ scoped RBAC       │
            │                  AgentMode CRD    OPA          kube-apiserver           │
            │  state: Qdrant (RAG) · audit log (HMAC) · calibration / baselines DB    │
            └────────────────────────────────────────────────────────────────────────┘
```

The loop has a name: **Observe → Predict → Heal → Learn**. Everything runs
**in-cluster**. The only outbound traffic is optional (model/image pulls at
install); at steady state Keos is self-contained — essential for air-gapped
sites.

## 2. The self-healing loop (the agent graph)

The heart of Keos is an explicit **LangGraph state machine** — not a free-form
ReAct loop. Every incident flows through the same nine nodes, each a pure
`state → state` function that emits one audit record:

```
triage → retrieve_context → k8sgpt_analyze → diagnose ─(confidence < threshold)─► escalate ─► END
                                                  │
                                                  ▼
                                            plan_action → policy_gate ─(deny)─► escalate ─► END
                                                              │ (allow)
                                                              ▼
                                                          execute → verify ─(unhealed)─► escalate ─► END
                                                                        │ (healed)
                                                                        ▼
                                                                       END
```

| Node | Responsibility |
|---|---|
| **triage** | Parse the event → severity, namespace, pod, rule, **domain** (`sre` / `security`). Skips stale SRE events (a pod that already recovered) — but never skips a security event for being "healthy". |
| **retrieve_context** | Embed the event, pull top runbooks from Qdrant, fetch `kubectl describe` for the target. |
| **k8sgpt_analyze** | Enrich with K8sGPT structured findings (analyzers, not summaries). Degrades gracefully if K8sGPT is down. |
| **diagnose** | LLM → `{summary, root_cause, confidence, evidence[]}`. Retries once on invalid JSON, then escalates. |
| **plan_action** | LLM constrained to the **tool catalog** → `{tool, args, scope, reversible}`. |
| **policy_gate** | Build the OPA input, evaluate the severity × action matrix at the current mode. Deny → escalate. |
| **execute** | In `active` mode, dispatch through the MCP server (defense-in-depth: MCP re-checks OPA). In `observe-only`, dry-run. |
| **verify** | Poll the cluster ~30 s; set `verification.healed`. Unhealed → escalate. |
| **escalate** | Page a human / the IR channel; mark the incident escalated. |

On CPU the full loop is minutes; on a GPU edge node (Jetson Orin) it is
**seconds** end to end.

### Action catalog

| Domain | Tools |
|---|---|
| **SRE** | `restart_pod`, `rollout_undo`, `scale_deployment`, `cordon_node`/`uncordon_node`, `drain_node`, `evict_workload`, `tune_hpa`, `set_resources`, `revert_drift`, `page_human` |
| **Security** | `isolate_pod`, `apply_network_policy`, `quarantine_pod`, `kill_pod`, `capture_forensics`, `page_security` |

Containment tools are **reversible** (delete the NetworkPolicy + strip the
label) except `kill_pod`. Reversal is one command: `keosctl quarantine release`.

## 3. The safety mesh

Autonomy is only safe because four independent controls gate every action:

1. **AgentMode CRD** (cluster-scoped kill-switch) — `observe-only` (dry-run,
   default), `active` (acts after the gate), `frozen` (no-op). Read at the
   *moment of execution*, so flipping it mid-incident takes effect immediately.
2. **OPA policy gate** — a Rego **severity × action matrix**. Each tool has a
   required autonomy level per severity; containment is denied on weak signals,
   allowed at high/critical. Hard-deny rules protect `kube-system`/`kube-public`
   and block any action outside `active` mode. The matrix runs in *both* the
   agent and the MCP server — they must agree, or it escalates.
3. **Scoped RBAC** — the ServiceAccount can do only what the catalog needs
   (patch pods, manage NetworkPolicies, read across namespaces) and nothing in
   protected namespaces.
4. **HMAC-signed audit chain** — every transition is one JSON line signed with
   HMAC-SHA256 over canonical JSON. `keosctl audit verify` walks the chain and
   flags any tampered record. This is the forensic guarantee.

Per-rule **confidence calibration** (learned from graded outcomes) decides when
a diagnosis is trustworthy enough to act vs. escalate.

## 4. Components

| Pod | Role |
|---|---|
| **api** | FastAPI — REST + WebSocket, the agent runtime (`--serve`), the Falco webhook, the pipeline trace bus. |
| **mcp-server** | Model-Context-Protocol server exposing the action catalog; the OPA-gated execution boundary (also drivable from Claude Desktop). |
| **frontend** | React SPA (Cluster, Incidents, Pipeline, Use Cases, Control, Settings), served by nginx. |
| **caddy** | Single-URL reverse proxy (`/` → SPA, `/api` + `/ws` → api). |
| **ollama** | Local LLM inference (qwen2.5 chat + nomic-embed). GPU-offloaded when available. |
| **qdrant** | Vector store for the runbook corpus (RAG). |
| **nats** | JetStream durable event bus. |
| **opa** | Policy engine (the Rego matrix). |
| *falco / tetragon* | Optional eBPF runtime-security sensors. |
| *chaos-mesh* | Optional fault injection driving the Use-Case demos. |

First-party images: `keos-api`, `keos-mcp-server`, `keos-frontend` (multi-arch
amd64 + arm64).

## 5. Data planes

- **Event plane** — kubelet events (debounced observer), Falco/Tetragon
  detections (falcosidekick → the api webhook), and synthetic Use-Case events
  land on NATS `infra.events.*` / `security.events.*`; the agent is a durable
  JetStream consumer.
- **Knowledge plane** — runbooks embedded into Qdrant; `diagnose` retrieves the
  top matches per domain.
- **State plane** — the HMAC audit log, the per-rule calibration DB, the ML
  baselines DB, finished incidents, and pipeline traces (PVC-backed; captured by
  `keosctl backup`).

## 6. The security domain

Beyond SRE self-healing, Keos runs a parallel **runtime-security** pipeline
(same graph, `domain=security`): Falco/Tetragon detect (reverse shell, miner,
priv-esc, exfil) → falcosidekick forwards the alert with a bearer token to the
api webhook → it becomes a `security.events.*` incident → diagnose → OPA-gated
**containment** (`isolate_pod` writes a deny-all NetworkPolicy + quarantine
label) → signed audit. Sensors are capability-gated by kernel: `modern_ebpf`
(BTF kernels), `ebpf-prebuilt` (BTF-less, e.g. Jetson L4T — a probe built once
per kernel via `keosctl falco-probe build`), or `off` (synthetic-event demo).

## 7. Continuous intelligence

- **Confidence calibration** — per-rule thresholds learned from graded outcomes.
- **ML baselines** — rolling baselines of cluster metrics for anomaly framing.
- **Predictive forecasting** — surfaces likely surges; feeds `tune_hpa` /
  `set_resources` / `evict_workload`.
- **GitOps drift detection** — watches a source-of-truth and reverts/triages
  drift in real time (complements the *scheduled* GitOps delivery in §8).

## 8. Deployment & delivery

Keos installs as a single Helm chart (`pocteo`) onto any conformant cluster —
k3s, Talos, Kairos, kubeadm, k3d, EKS/GKE/AKS — on amd64 or arm64, with optional
GPU. For the **private-registry, air-gapped** posture, images + the OCI chart
live only in a local `:5000` registry, and updates flow by **pull-based GitOps**:
Flux on the cluster reconciles the desired state (chart + SOPS-encrypted values)
from the registry — no inbound access. See [Operations](./OPERATIONS.md) for the
release + GitOps flow and [Installation](./INSTALLATION.md) for every target.

## 9. Design principles

- **Closed-form graph, not an open agent** — every path is enumerable and auditable.
- **Policy as data** — the risk matrix is Rego, not buried in prompts.
- **Reversible by default** — containment can always be undone; the kill-switch is one CRD field.
- **Edge-first** — CPU-capable, GPU-accelerated, ARM64-native, air-gap-ready, no cloud dependency.
- **Everything signed** — the audit chain is the source of truth for "what did the agent do, and why".
