# Keos SRE Agent — Architecture

This document is the **bible**. Read it once, in order, and you will be able
to reason about every other file in the repo without surprise. Every other
doc (USER_GUIDE, INSTALL, TALOS, CONFIG_REFERENCE) refers back here.

It is built in layers:

1. **The 10-second pitch** — one diagram, one paragraph.
2. **The macro architecture** — every running container and how they talk.
3. **The agent loop** — the LangGraph state machine, node by node.
4. **The safety mesh** — OPA policy, AgentMode CRD, MCP boundary, HMAC audit.
5. **The data planes** — RAG store, telemetry bus, audit chain, WebSocket fan-out.
6. **The deployment shapes** — laptop, edge k3s, Talos, air-gapped.
7. **The request lifecycle** — one event traced from publish to healed.
8. **Continuous intelligence** — Phase 12-15 loops: calibration, ML
   baselines, predictive forecasting, GitOps drift detection.

> **Convention.** All diagrams are Mermaid. They render natively in
> GitHub, GitLab, VS Code (with the Mermaid extension), and most IDE
> previewers. If a diagram looks broken in your viewer, copy the fenced
> block into <https://mermaid.live> to inspect it.

---

## 1. The 10-second pitch

```mermaid
flowchart LR
    T[Telemetry event<br/>chaos / kubelet / metrics] --> A((Keos<br/>SRE Agent))
    A -->|read-only signals| K[Kubernetes API]
    A -->|policy check| O[OPA]
    A -->|scoped action| K
    A -->|signed record| L[(Audit log<br/>HMAC chain)]
    A -.->|escalate| H[Human / Slack]
```

The Keos SRE Agent receives an infrastructure event, decides whether to
act, gates the action through Open Policy Agent, executes under scoped
RBAC, verifies the cluster healed, and writes a tamper-evident audit
record. If confidence is low, the policy denies, the action fails, or the
cluster didn't heal — it escalates to a human instead.

The loop has a name: **Observe → Predict → Heal → Learn**.

---

## 2. Macro architecture

Everything that runs when you `make demo-ui` on a laptop. On a real
cluster the same components run as Pods; only the boundaries change
(see §6).

```mermaid
flowchart TB
    subgraph User["User"]
        Browser[Browser<br/>SPA]
    end

    subgraph Edge["Reverse proxy"]
        Caddy[Caddy :80]
    end

    subgraph FrontendSvc["Frontend"]
        SPA[nginx<br/>React SPA static]
    end

    subgraph Backend["Control plane"]
        API[FastAPI<br/>:8000<br/>+ WebSocket<br/>+ Agent serve loop]
        MCP[MCP Server<br/>:8090<br/>action tools]
    end

    subgraph Inference["AI inference"]
        Ollama[Ollama<br/>:11434<br/>Qwen2.5-3B + nomic-embed]
        Qdrant[Qdrant<br/>:6333<br/>runbook vectors]
    end

    subgraph Policy["Safety mesh"]
        OPA[OPA<br/>:8181<br/>agent.rego]
    end

    subgraph Bus["Event bus"]
        NATS[NATS JetStream<br/>:4222<br/>INFRA stream]
    end

    subgraph K8s["Kubernetes (k3d / k3s / Talos)"]
        APIserver[kube-apiserver]
        AgentMode[(AgentMode CRD<br/>observe/active/frozen)]
        Workloads[workloads ns<br/>real pods]
        Chaos[Chaos Mesh<br/>controller + daemon]
        Diag[Diagnostic signals<br/>K8sGPT MCP :8089]
    end

    Browser --> Caddy
    Caddy -->|/| SPA
    Caddy -->|/api| API
    Caddy -->|/ws| API

    API <-->|tool calls| MCP
    API --> Ollama
    API --> Qdrant
    API --> OPA
    API --> NATS
    API --> Diag
    API <-->|watch CRD<br/>+ scoped kubectl| APIserver

    MCP --> OPA
    MCP --> APIserver

    APIserver --> AgentMode
    APIserver --> Workloads
    Chaos --> Workloads
    APIserver -.->|events| API

    classDef store fill:#1e293b,stroke:#475569,color:#e2e8f0
    classDef compute fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    classDef safety fill:#7c2d12,stroke:#ea580c,color:#fed7aa
    classDef k8s fill:#14532d,stroke:#16a34a,color:#bbf7d0
    class Ollama,Qdrant,NATS store
    class API,MCP,SPA,Caddy compute
    class OPA safety
    class APIserver,AgentMode,Workloads,Chaos,Diag k8s
```

### Why each component exists

| Component | Why we need it | What it gives up |
|---|---|---|
| **Caddy** | Single URL (`http://localhost`), terminates TLS in prod, WebSocket-aware reverse proxy. | One more hop. We accept it because it makes the demo URL trivial. |
| **SPA (React + Vite)** | Multi-view UI without a heavy server framework. Bilingual EN/FR via i18next. | Bearer-token auth means a paste-from-`.env.local` step on first load. |
| **FastAPI + Agent serve loop** | One process holds: the LangGraph runtime, the NATS consumer, the WebSocket multiplexer, the ModeStore K8s watcher, the audit logger, the chaos observer. Single asyncio event loop = fewer race conditions, fewer ports. | If this process dies, all the above stop together. (Mitigation: K8s Deployment + liveness probe in prod.) |
| **MCP server** | The action catalog is reachable from any MCP client (Claude Desktop, kagent, Cursor) under the same OPA + RBAC + audit guarantees the agent itself uses. The agent's `execute` node calls *its own* MCP server — the abstraction is real. | One more network hop on the act path. Defense-in-depth justifies it. |
| **Ollama** | Local LLM inference. CPU-only is fine for Qwen2.5-3B (~1.9 GB quantized). No GPU dependency = runs on Jetson and laptop alike. | Slow first call (~60 s warmup). Mitigated by `keep_alive=30m`. |
| **Qdrant** | Vector store for the RAG corpus (7 SRE runbooks). 768-dim cosine, in-process FAISS-quality without the C++ build pain. | Another stateful service to back up. |
| **NATS JetStream** | Durable event bus. The agent is a JetStream consumer with at-least-once delivery — no events lost on restart. Subject is `infra.events.<kind>`. | Single point of failure (mitigated by JetStream's replication in cluster mode). |
| **OPA** | Externalised policy. The action × severity × mode matrix lives in `policies/agent.rego` — auditable, hot-reloadable, decoupled from agent code. | One more service. The HTTP call is sub-millisecond. |
| **AgentMode CRD** | Kill-switch. Cluster-scoped singleton (`default`). Flipping `spec.mode` to `frozen` or `observe-only` immediately disarms the agent without restarting anything. | Adds a CRD to install. Worth it. |
| **Chaos Mesh** | Real fault injection (PodChaos, StressChaos, NetworkChaos, IOChaos) instead of synthetic Falco-shaped events. The agent observes *real* kubelet reactions. | Cluster-level install; needs the right containerd socket path on Talos (see TALOS.md). |
| **K8sGPT (MCP)** | Independent diagnostic signal source. Rule-based, deterministic, reads the live API. The LLM consumes pre-digested findings instead of inventing them. | One more service. The agent gracefully degrades if it's unreachable. |

---

## 3. The agent loop (LangGraph)

This is the heart. **Ten nodes** (Phase 13 inserted
`enrich_with_baselines` between `k8sgpt_analyze` and `diagnose`), four
phases, three escape hatches to `escalate`. Each node is a pure async
function that takes the `IncidentState` TypedDict and returns a
partial dict that LangGraph merges into the state.

```mermaid
flowchart TD
    START([NATS event<br/>infra.events.kind<br/>reactive · forecasted · drift]) --> Triage

    subgraph Observe["OBSERVE — gather"]
        Triage[triage<br/>parse event<br/>+ severity / ns / pod]
        Retrieve[retrieve_context<br/>RAG top-3 from Qdrant<br/>+ kubectl describe pod]
        K8sgpt[k8sgpt_analyze<br/>cluster diagnostic signals<br/>K8sGPT MCP]
        Baselines[enrich_with_baselines<br/>σ-above-baseline<br/>+ 5/15/60 min forecasts]
    end

    subgraph Predict["PREDICT — reason"]
        Diagnose[diagnose<br/>LLM JSON<br/>summary + root_cause<br/>+ confidence]
        Plan[plan_action<br/>LLM JSON<br/>pick tool from catalog]
    end

    subgraph Heal["HEAL — act"]
        Gate[policy_gate<br/>OPA: agent_mode<br/>+ severity x action]
        Execute[execute<br/>call MCP tool<br/>or dry-run<br/>if observe-only]
    end

    subgraph Learn["LEARN — close"]
        Verify[verify<br/>poll kubectl<br/>up to 30s]
        Escalate[escalate<br/>page_human<br/>+ persist incident]
        ENDok([END healed])
        ENDesc([END escalated])
    end

    Triage --> Retrieve --> K8sgpt --> Baselines --> Diagnose
    Diagnose -->|confidence < threshold<br/>per-rule from calibration| Escalate
    Diagnose -->|confidence >= threshold| Plan
    Plan --> Gate
    Gate -->|deny| Escalate
    Gate -->|allow| Execute
    Execute -->|error| Escalate
    Execute --> Verify
    Verify -->|not healed| Escalate
    Verify -->|healed| ENDok
    Escalate --> ENDesc

    classDef observe fill:#78350f,stroke:#f59e0b,color:#fef3c7
    classDef predict fill:#1e3a8a,stroke:#3b82f6,color:#dbeafe
    classDef heal fill:#1d4ed8,stroke:#60a5fa,color:#dbeafe
    classDef learn fill:#14532d,stroke:#16a34a,color:#bbf7d0
    classDef terminal fill:#1e293b,stroke:#64748b,color:#f1f5f9
    class Triage,Retrieve,K8sgpt,Baselines observe
    class Diagnose,Plan predict
    class Gate,Execute heal
    class Verify,Escalate learn
    class START,ENDok,ENDesc terminal
```

### Node-by-node

| Node | Phase | Reads | Writes | Can route to `escalate`? |
|---|---|---|---|---|
| `triage` | observe | NATS event payload | `severity`, `namespace`, `pod`, `rule_id` | no |
| `retrieve_context` | observe | rule + output text | `rag_hits` (top-3 chunks), `pod_describe` | no |
| `k8sgpt_analyze` | observe | namespace, pod | `k8sgpt_findings: list[Finding]` (empty on failure — soft degrade) | no |
| `enrich_with_baselines` | observe | namespace, pod | `baselines: list[BaselineSnapshot]` + `forecasts: list[Forecast]` (empty on failure — soft degrade) | no |
| `diagnose` | predict | event + RAG + findings + baselines + describe | `diagnosis: {summary, root_cause, confidence, evidence, notes}` | **yes** if `confidence < threshold` (per-rule from Phase 12 calibration; falls back to global 0.6) |
| `plan_action` | predict | diagnosis + tool catalog (11 tools) | `proposed_action: {tool, args, scope, reversible}` | no (constrained by catalog) |
| `policy_gate` | heal | `proposed_action`, `severity`, `mode` | `policy_decision: {allow, reasons[]}` | **yes** if `allow=false` |
| `execute` | heal | `proposed_action`, `mode` | `action_result: {ok, dry_run, output, error}` | **yes** if `ok=false` |
| `verify` | learn | `action_result.target` | `verification: {healed, observations}` | **yes** if `healed=false` |
| `escalate` | learn | full state | `escalated=true`, emits `page_human` | terminal |

### Why an LLM at all?

Two narrow places — both with tight schemas and a retry-or-escalate hook:

```mermaid
sequenceDiagram
    participant G as Graph
    participant L as LLM (Qwen2.5-3B JSON mode)
    participant P as Pydantic

    G->>L: diagnose(event + RAG + findings)
    L-->>G: JSON string
    G->>P: validate(Diagnosis)
    alt valid
        P-->>G: Diagnosis model
    else invalid
        G->>L: retry with "your JSON was invalid"
        L-->>G: JSON string (attempt 2)
        G->>P: validate
        alt still invalid
            P-->>G: ValidationError
            G->>G: route to escalate
        end
    end
```

The LLM never *acts* — it produces a typed plan that the policy gate
either accepts or rejects. If the model hallucinates a tool name not in
the catalog, the Pydantic `ToolName = Literal[...]` validator catches it
and we escalate. Total cost of LLM failure: one human page, zero
cluster damage.

---

## 4. The safety mesh

Four orthogonal guards. Each can independently veto an action. All four
must agree for anything destructive to happen.

```mermaid
flowchart LR
    Action[Proposed action] --> M{AgentMode<br/>== active?}
    M -->|no| Den1[deny: mode]
    M -->|yes| O{OPA matrix<br/>level <= 2?}
    O -->|no| Den2[deny: matrix]
    O -->|yes| N{namespace<br/>not protected?}
    N -->|no| Den3[deny: ns kube-system / kube-public]
    N -->|yes| R[RBAC scope<br/>self-healing-agent SA]
    R -->|kube-apiserver allows| Exec[execute via MCP]
    R -->|kube-apiserver forbids| Den4[deny: RBAC]
    Den1 --> Esc[escalate]
    Den2 --> Esc
    Den3 --> Esc
    Den4 --> Esc

    classDef guard fill:#7c2d12,stroke:#ea580c,color:#fed7aa
    classDef deny fill:#7f1d1d,stroke:#dc2626,color:#fecaca
    classDef ok fill:#14532d,stroke:#16a34a,color:#bbf7d0
    class M,O,N,R guard
    class Den1,Den2,Den3,Den4 deny
    class Exec ok
```

### Layer 1 — AgentMode CRD (the kill-switch)

Cluster-scoped CR, singleton `default`. `spec.mode ∈ {observe-only, active, frozen}`.

```yaml
apiVersion: security.poc/v1alpha1
kind: AgentMode
metadata:
  name: default
spec:
  mode: observe-only   # | active | frozen
```

`mode_store.py` runs a kubernetes-client watch on a daemon thread. Reads
from the graph are O(nanoseconds); mode flips propagate in <1 s. The
`execute` node reads `await mode_store.get()` at the moment of execution
— so a `kubectl patch agentmode default …` mid-incident takes effect on
the next graph step, not the next event.

### Layer 2 — OPA matrix (the rulebook)

Lives in `policies/agent.rego`. Action × severity matrix:

| Action | scope | low | medium | high | critical |
|---|---|---|---|---|---|
| `restart_pod` | pod | L1 | L1 | L2 | L2 |
| `rollout_undo` | deploy | L3 | L2 | L1 | L1 |
| `scale_deployment` | deploy | L2 | L1 | L1 | L1 |
| `cordon_node` | node | L3 | L2 | L1 | L1 |
| `drain_node` | node | L4 | L3 | L2 | L1 |
| `page_human` | n/a | L1 | L1 | L1 | L1 |

`L1`–`L2` → allow. `L3`–`L4` → deny. Hard-denies (regardless of matrix):

- target namespace ∈ `{kube-system, kube-public}`
- `agent_mode != "active"`
- unknown action or unknown severity

The matrix is **inline** in Rego — no `data.json` indirection. Edit one
file, `docker compose restart opa`, done.

### Layer 3 — Namespace boundary (RBAC)

The `self-healing-agent` ServiceAccount in `security` namespace is bound
to a Role in `workloads` only:

```yaml
# Role (workloads namespace)
- apiGroups: [""]
  resources: [pods]
  verbs: [get, list, watch, delete, patch]
- apiGroups: [apps]
  resources: [deployments, deployments/scale]
  verbs: [get, list, patch]
# ClusterRole (for node-level actions)
- apiGroups: [""]
  resources: [nodes]
  verbs: [patch, update]   # cordon/drain
```

Even if OPA mis-allows a `delete pod` in `kube-system`, the API server
denies it: the SA has no Role binding there.

### Layer 4 — MCP boundary (defense-in-depth)

The action path is **agent → MCP → OPA → kubectl**, not
**agent → OPA → kubectl**. Both the agent's `policy_gate` node *and* the
MCP server's pre-execute check call OPA. If the decisions disagree the
event is logged loudly + the action is denied.

```mermaid
sequenceDiagram
    participant A as Agent (execute node)
    participant M as MCP Server
    participant O as OPA
    participant K as kube-apiserver
    participant L as Audit log

    A->>O: 1. policy_gate evaluate
    O-->>A: allow

    A->>M: call_tool restart_pod ns=workloads pod=nginx-xxx
    M->>O: 2. defense-in-depth evaluate
    O-->>M: allow
    M->>K: kubectl delete pod nginx-xxx -n workloads
    K-->>M: deleted
    M->>L: emit source=mcp action=restart_pod result=ok
    M-->>A: result

    A->>L: emit source=agent execute_done
```

Why two checks? **Threat model.** If an attacker compromises the agent
process but not the MCP server, they can't bypass policy. If they
compromise the MCP server but not OPA, they can't bypass policy. The
agent itself is *also* a client — proving the abstraction. Claude
Desktop or kagent calling the same tools are subject to the same gate.

---

## 5. The data planes

### 5.1 Telemetry bus (NATS JetStream)

```mermaid
flowchart LR
    subgraph Sources["Event sources"]
        ChaosObs[chaos_observer<br/>K8s event watcher]
        Synth[synthetic_events<br/>CLI]
        UC[usecase_runner<br/>+ chaos CR]
    end
    subgraph Stream["INFRA stream"]
        S[(NATS JetStream<br/>subject infra.events.&gt;<br/>durable consumer pocteo-agent)]
    end
    subgraph Consumer["Agent"]
        Sub[graph --serve<br/>at-least-once]
    end

    ChaosObs -->|infra.events.oom_killed<br/>infra.events.crashloop<br/>infra.events.image_pull_failure<br/>infra.events.node_not_ready| S
    Synth -->|infra.events.&lt;kind&gt;| S
    UC -->|applies ChaosExperiment CR<br/>kubelet emits event<br/>chaos_observer publishes| ChaosObs
    S --> Sub
```

The `chaos_observer` daemon watches `core/v1/Event` across `workloads`
and translates Kubernetes events (`OOMKilled`, `BackOff`,
`ImagePullBackOff`, `NodeNotReady`, …) into NATS messages. Debounced at
one publish per (pod, reason) per 60 s to avoid noise.

### 5.2 RAG store (Qdrant + Ollama embeddings)

```mermaid
flowchart LR
    subgraph Ingest["Seed-time (make seed)"]
        RB[agent/runbooks/*.md] --> SR[seed_rag.py]
        SR --> EmbA[Ollama<br/>nomic-embed-text]
        EmbA --> UP[upsert to Qdrant<br/>collection runbooks<br/>768-dim COSINE]
    end
    subgraph Query["Query-time (retrieve_context node)"]
        Evt[event.rule + event.output] --> EmbB[Ollama<br/>nomic-embed-text]
        EmbB --> Q[Qdrant<br/>top-3]
        Q --> Hits[rag_hits<br/>fed to diagnose]
    end
```

Each runbook is one chunk (they're small — ~30 lines of Markdown). The
collection is `runbooks`, 768-dim, COSINE. `seed_one(slug, text)` allows
hot-adding a runbook at use-case-run time.

### 5.3 Audit chain (HMAC over canonical JSON)

```mermaid
flowchart LR
    subgraph Producer["Per node entry / exit"]
        N[node start / end] --> Rec[record:<br/>ts incident_id<br/>event_type payload]
        Rec --> Canon[canonical JSON<br/>sorted keys]
        Canon --> H[HMAC-SHA256<br/>AUDIT_HMAC_KEY]
        H --> Line[append line<br/>to audit.log]
    end

    subgraph Verifier["Offline verifier"]
        F[audit.log] --> P[parse]
        P --> RC[re-canonicalize]
        RC --> RH[re-HMAC]
        RH --> Cmp{matches sig?}
        Cmp -->|yes| OK[record intact]
        Cmp -->|no| BAD[tampered<br/>fail loudly]
    end
```

HMAC over **canonical JSON** (sorted keys, no whitespace) so verification
is reproducible regardless of dict insertion order. The key is in
`AUDIT_HMAC_KEY`; rotate before any non-laptop use. Verifier:

```python
from agent.audit import verify_audit_log
n_records, broken = verify_audit_log("agent/audit.log", key)
assert broken == []
```

### 5.4 WebSocket fan-out (live UI updates)

```mermaid
flowchart LR
    subgraph InProcess["In agent process"]
        TB[TraceBus<br/>asyncio pub/sub]
        MS[ModeStore<br/>change callbacks]
        NS[NATS subscriber<br/>infra.events &gt;]
        AS[audit tail<br/>append-only]
        KI[K8s informer<br/>1Hz debounce]
    end
    subgraph WS["WebSocket bridge"]
        Mux[ws_bridge<br/>multiplexes topics]
    end
    subgraph Client["Browser"]
        SPA[SPA hooks<br/>usePipelineState<br/>useClusterState]
    end

    TB -->|pipeline.&lt;id&gt;| Mux
    NS -->|falco| Mux
    KI -->|cluster| Mux
    MS -->|mode| Mux
    AS -->|audit| Mux
    Mux -->|JSON frames| SPA
```

Clients subscribe to topics over `/ws/events`. Each LangGraph node emits
`pipeline.<incident_id>` start/end events through the TraceBus
synchronously — what the UI animates is the same record that's in the
audit log, byte for byte.

---

## 6. Deployment shapes

Same Pods, same OPA, same audit. The only differences are *where the
containers run* and *who routes traffic*.

```mermaid
flowchart TB
    subgraph Laptop["Laptop demo (docker-compose + k3d)"]
        L_Caddy[caddy:80]
        L_API[api]
        L_MCP[mcp-server]
        L_FE[frontend]
        L_Stores[(ollama / qdrant / nats / opa)]
        L_K3D[k3d cluster<br/>+ chaos-mesh]
        L_Caddy --> L_FE
        L_Caddy --> L_API
        L_API --> L_Stores
        L_API --> L_MCP
        L_API -. host.docker.internal .-> L_K3D
    end

    subgraph Edge["Edge K8s (k3s / Talos / EKS, via Helm)"]
        E_Ing[Ingress / LoadBalancer]
        E_Caddy[caddy Deployment]
        E_API[api Deployment]
        E_MCP[mcp-server Deployment]
        E_FE[frontend Deployment]
        E_Stores[ollama / qdrant / nats / opa<br/>StatefulSets + PVCs]
        E_Agent[agent loop runs<br/>INSIDE the cluster<br/>via in-cluster SA]
        E_Chaos[chaos-mesh<br/>sub-chart]
        E_Ing --> E_Caddy
        E_Caddy --> E_FE
        E_Caddy --> E_API
        E_API --> E_Stores
        E_API --> E_MCP
        E_API --- E_Agent
        E_Agent --> E_Chaos
    end
```

| Aspect | Laptop | Edge K8s |
|---|---|---|
| Container runtime | Docker Desktop (Linux containers) | containerd / cri-o |
| Persistence | Docker named volumes | PVCs (any conformant StorageClass) |
| Cluster auth | Host kubeconfig → `host.docker.internal:6550` | In-cluster ServiceAccount |
| Reverse proxy | Caddy compose service :80 | Caddy Deployment + Ingress (cert-manager optional) |
| Bearer token | `.env.local` (gitignored) | Secret `pocteo-pocteo-auth` |
| Mode CR | Same CRD | Same CRD |
| Chaos Mesh | Helm install during `make cluster` | Helm sub-chart in the Keos chart |

Talos specifics (containerd socket at `/run/containerd/containerd.sock`,
air-gapped image cache via `talosctl image cache`) are documented in
[TALOS.md](TALOS.md). The Helm chart templates take a `runtime` value to
toggle between k3s / containerd / cri-o socket paths.

---

## 7. Request lifecycle — one event end to end

This is what actually happens when you click **Run** on a use case named
"Memory leak". Follow it once in this doc; you'll never need to ask "who
calls what" again.

```mermaid
sequenceDiagram
    autonumber
    participant U as User (SPA)
    participant API as FastAPI
    participant UR as usecase_runner
    participant K as kube-apiserver
    participant CM as Chaos Mesh
    participant Kub as kubelet
    participant CO as chaos_observer
    participant N as NATS
    participant G as LangGraph
    participant O as Ollama
    participant Q as Qdrant
    participant KG as K8sGPT
    participant OPA
    participant MCP
    participant A as Audit log
    participant WS as WebSocket clients

    U->>API: POST /api/usecases/lib-memory-leak/run
    API->>UR: run(use_case)
    UR->>K: apply ChaosExperiment (StressChaos, 30s TTL)
    K->>CM: ChaosExperiment CR
    CM->>Kub: inject memory stress into nginx-demo-xxx
    Kub-->>K: emit Event reason=OOMKilled pod=nginx-demo-xxx
    K-->>CO: watch event
    CO->>N: publish infra.events.oom_killed
    N->>G: deliver to durable consumer pocteo-agent

    G->>A: pipeline.triage.start
    G->>WS: trace event
    G->>G: triage -> severity=high, ns=workloads, pod=nginx-demo-xxx
    G->>A: pipeline.triage.end

    G->>O: embed("OOMKilled + output")
    O-->>G: vector
    G->>Q: search top-3 in runbooks
    Q-->>G: 001-memory-leak.md + 002-crashloop.md + ...
    G->>K: kubectl describe pod nginx-demo-xxx
    K-->>G: pod spec + events
    G->>A: pipeline.retrieve_context.end

    G->>KG: analyze(namespace=workloads)
    KG-->>G: findings: [Pod OOMKilled, container memory limit too low]
    G->>A: pipeline.k8sgpt_analyze.end

    G->>O: chat(diagnose, evidence)
    O-->>G: {confidence: 0.82, root_cause: "...", ...}
    G->>O: chat(plan_action, tool catalog)
    O-->>G: {tool: rollout_undo, args: {deployment: nginx-demo, ns: workloads}}
    G->>A: pipeline.diagnose.end + pipeline.plan_action.end

    G->>OPA: evaluate {action: rollout_undo, sev: high, mode: active, target: workloads}
    OPA-->>G: allow
    G->>A: pipeline.policy_gate.end allow

    G->>MCP: call_tool rollout_undo
    MCP->>OPA: evaluate (defense-in-depth)
    OPA-->>MCP: allow
    MCP->>K: kubectl rollout undo deploy/nginx-demo -n workloads
    K-->>MCP: rolled back
    MCP->>A: source=mcp action=rollout_undo result=ok
    MCP-->>G: result
    G->>A: pipeline.execute.end

    G->>K: poll pod readiness up to 30s
    K-->>G: 3/3 Ready
    G->>A: pipeline.verify.end healed=true
    G->>WS: incident.healed
    Note over G,WS: SPA timeline updates green; <br/>telemetry sidebar shows pods Ready
```

### Inputs and outputs by step (the cheat sheet)

| Step | Input | Output | Stored in |
|---|---|---|---|
| 1–3 | Use case definition | ChaosExperiment CR | k8s (TTL 30s) |
| 4–7 | StressChaos | OOMKilled event | k8s event stream |
| 8–9 | K8s event | `infra.events.oom_killed` | NATS JetStream |
| 10–12 | NATS event | `IncidentState` initialised | in-memory + audit |
| 13–17 | embed + vector search | RAG hits + describe | state |
| 18–19 | live cluster | K8sGPT findings | state |
| 20–22 | event + RAG + findings + describe | Diagnosis + ProposedAction | state + audit |
| 23–25 | proposed action | PolicyDecision | OPA → state + audit |
| 26–32 | proposed action | ActionResult | MCP → kubectl → audit |
| 33–36 | target | Verification (healed=true/false) | state + audit |
| WS | every node start/end | UI frames | TraceBus → /ws/events |

---

## 8. Continuous intelligence — Phases 12-15

The reactive loop (§3-§7) is the **floor**. The four background
intelligence layers below are what move the agent from "useful demo"
to "production-grade autonomous SRE". Each one is independently
optional — kill any of them via env var and the reactive layer keeps
running unchanged — but stacked, they lift the median diagnose
confidence from ~0.65 toward the 0.90 design target.

```mermaid
flowchart LR
    subgraph Reactive["Reactive (Phase 4-9)"]
        Ev[infra.events.<br/>OOMKilled · BackOff · ...]
    end

    subgraph Predictive["Predictive (Phase 14)"]
        F[forecasting.scheduler<br/>60s tick] --> Fev[infra.events.<br/>forecasted_*]
    end

    subgraph Drift["Drift (Phase 15)"]
        D[drift.detector<br/>120s tick] --> Dev[infra.events.<br/>drift.*]
    end

    subgraph Baselines["Baselines (Phase 13)"]
        L[baselines.learner<br/>60s scrape · 600s fit]
        B[(baselines.db<br/>EWMA + DoW median)]
        L --> B
    end

    subgraph Calibration["Calibration (Phase 12)"]
        G[calibration.grader<br/>after verify]
        S[(calibration.db<br/>per-rule threshold)]
        G --> S
    end

    Ev --> N[(NATS INFRA)]
    Fev --> N
    Dev --> N
    N --> GR[LangGraph]
    B --enrich_with_baselines--> GR
    B --feeds--> F
    GR --rule outcome--> G
    S --per-rule threshold--> GR
```

### 8.1 Per-rule calibration (Phase 12)

| What | Where | Why it matters |
|---|---|---|
| Tracks per-rule diagnose-confidence histograms + accuracy after `verify` | `agent/calibration/store.py` (SQLite at `data/calibration.db`) | Without per-rule numbers, the agent can't tell a "noisy" rule (LLM is often wrong) from a well-calibrated one (LLM is reliably right). |
| Learns a per-rule threshold = p25 of the historical confidence distribution of correct diagnoses | `agent/calibration/grader.py` | Noisy rules end up with a high threshold → more escalations on them. Well-calibrated rules get a permissive threshold → more autonomous heals. |
| Operator override per rule (pin a manual threshold from the SPA) | Settings → Calibration tab; `POST /api/calibration/{rule}/threshold` | Operator's runtime knowledge beats the learner during the first few weeks of deployment. |
| Exposes Prometheus histograms | `keos_diagnose_confidence{rule}` + `keos_diagnose_accuracy{rule}` | Plug into Grafana/Alertmanager without writing a custom exporter. |

### 8.2 ML baselines (Phase 13)

| What | Where | Why it matters |
|---|---|---|
| Scrapes Prometheus every 60 s for a fixed set of cluster metrics | `agent/baselines/learner.py` | The diagnose prompt needs numeric grounding. "Memory is 838 MiB" is meaningless; "Memory is 838 MiB, baseline 412 ± 18 MiB → 23 σ above" is actionable. |
| Fits a per-(metric, namespace, pod) baseline every 600 s | EWMA mean + DoW median (per hour-of-day) | EWMA captures short-term drift; DoW median captures seasonal cycles (Mon-9am ≠ Sat-3am). |
| `enrich_with_baselines` node injects σ-above-baseline into the diagnose user prompt | `agent/graph.py:node_enrich_with_baselines` | The LLM weights numeric anomaly evidence higher than text-only RAG hits. Single biggest accuracy lever in the project. |
| Prometheus source is pluggable | Compose-side `http://prometheus:9090` OR in-cluster `k8s-proxy://monitoring/prometheus:9090` via apiserver proxy (default in Phase 13c) | Edge clusters get the in-cluster path "for free"; laptop demos can still use the compose-side Prometheus. |
| Cold-start handling | Below 50 samples per key → "still learning" note in the prompt | Cold cluster doesn't poison the diagnose with garbage baselines. |

### 8.3 Predictive forecasting (Phase 14)

| What | Where | Why it matters |
|---|---|---|
| Linear-regression forecaster (60-sample OLS) | `agent/forecasting/forecaster.py` | At 5/15/60-min horizons short-term metric trends are linear-dominated; OLS is ~10 µs per fit. ARIMA / Holt-Winters would pull `statsmodels` (50+ MB) for negligible accuracy gain at these horizons. |
| Background scheduler (60 s tick) | `agent/forecasting/scheduler.py` | When any fitted baseline crosses its danger threshold within the horizon → publishes `infra.events.forecasted_<kind>` to NATS. The agent's existing graph consumer picks it up like any other event. |
| Severity step-down | Forecasted events fire at one severity LOWER than reactive equivalent | Conservative defaults: a forecasted_oom is "high" rather than "critical" so OPA's matrix gates the autonomous response more strictly. |
| Three new action tools | `tune_hpa`, `set_resources`, `evict_workload` (in `agent/tools.py` + `mcp_server/server.py` + OPA matrix entries) | The agent can act *preemptively* — raise HPA's `max` before a traffic surge, raise memory requests before an OOM, evict a noisy neighbour before it OOMs siblings. |
| R² floor | Forecasts with `R² < R2_MIN` are discarded | Seasonal load patterns + autoscaling-feedback patterns look non-linear at short horizons; the floor stops them from producing false alarms. |

### 8.4 GitOps drift detection (Phase 15)

| What | Where | Why it matters |
|---|---|---|
| `SourceOfTruth` protocol + `ManifestsSource` provider | `agent/drift/sources.py` | v1 reads a bind-mounted directory of YAML files. Future providers (ArgoCD `Application` CRs, Flux `Kustomization` CRs, raw `git clone`) implement the same protocol and slot in without touching detector/comparator. |
| Field-whitelisted comparator | `agent/drift/comparator.py` | Strips server-controlled fields (resourceVersion, managedFields, status) + controller-owned fields (HPA's spec.replicas, Service clusterIP). Per-manifest override via `pocteo.io/drift-ignore-fields` annotation. |
| Background detector (120 s tick + 600 s dedup) | `agent/drift/detector.py` | Per target: fetch live → compare → publish `infra.events.drift.<kind>` if drifted. Soft-fail per target — one broken manifest doesn't stop the rest. |
| New `revert_drift` action | `agent/tools.py::revert_drift` + `mcp_server` + OPA matrix | Re-applies the SoT manifest via `kubectl apply -f`. Defensive guards: refuses if the SoT doc's `kind` / `name` don't match the request (catches stale audit replays after the SoT changed). |
| Runbook 012 | `agent/runbooks/012-drift.md` (indexed in Qdrant via `make seed`) | The diagnose LLM retrieves this through the existing RAG path; covers `kubectl edit` vs controller writeback vs stale SoT diagnosis paths. |

### 8.5 How the four layers stack

The numbers below are design targets from the Phase 12+ plan, validated
empirically as each phase landed:

| Layer | Effort | Expected confidence delta |
|---|---|---|
| Phase 12 — calibration | 1-2 wk | Establishes baseline (≈ 0.65-0.75) — no lift on its own, but measurable. |
| Phase 13 — ML baselines | 3-4 wk | **+10-15 pp** on metric-driven rules (memory leaks, OOM, traffic). Largest single lever. |
| Phase 14 — predictive forecasting | 3-4 wk | **+3-5 pp** on traffic + rightsizing rules. More importantly: shifts the loop from reactive to proactive. |
| Phase 15 — GitOps drift | 2-3 wk | Broader event surface — neutral on median confidence but covers a class of incidents the reactive layer never sees. |

Phases 16-20 (continuous learning, admission webhook, edge resilience,
deep security, operator confidence-tuning UI) build on top of these
foundations. See the Phase 12+ plan for the path to ≥ 0.90 median.

### 8.6 Kill-switches

Each background loop has a single env-var disable:

| Layer | Disable | Effect |
|---|---|---|
| Calibration | `KEOS_CALIBRATION_ENABLED=0` | Falls back to global `AGENT_CONFIDENCE_THRESHOLD`. |
| Baselines | `PROMETHEUS_URL=""` | `enrich_with_baselines` no-ops; diagnose prompt has no σ context. |
| Forecasting | `FORECAST_ENABLED=0` | Scheduler doesn't start; only reactive + drift events flow. |
| Drift | `DRIFT_SOURCE_TYPE=none` | Detector doesn't start; only reactive + forecast events flow. |

All four are configurable from `values.yaml` (Helm) or `.env.local`
(laptop). Doctor reports each layer's state at boot so an operator
knows what's on.

---

## What you now know

If you can answer these in your own words, you understand the system:

1. **Where does an event enter the system?** NATS JetStream subject
   `infra.events.<kind>`, produced by `chaos_observer` watching K8s
   events. The agent is a durable consumer.
2. **Who decides what to do?** `diagnose` (LLM, advisory) → `plan_action`
   (LLM, constrained by `ToolName` literal). The LLM picks; OPA decides
   if it's allowed.
3. **What stops the agent from doing damage?** Four orthogonal guards:
   AgentMode CRD, OPA matrix, namespace whitelist, RBAC scope. Plus the
   MCP server re-checks OPA before any side effect.
4. **Where is the audit?** `agent/audit.log` (laptop) or
   `/var/log/pocteo/audit.log` (cluster Pod). One JSON object per line,
   HMAC-SHA256 over canonical JSON. Verifier function in `agent/audit.py`.
5. **How do I disarm it instantly?** `kubectl patch agentmode default
   --patch-file manifests/patches/mode-observe.yaml`. The watch picks
   it up in <1 s.
6. **What's the same and what's different on Talos?** Same Pods, same
   OPA, same audit. Different: containerd socket path
   (`/run/containerd/containerd.sock`), image pre-pull via `talosctl
   image cache`, in-cluster ServiceAccount instead of host kubeconfig.

If something else in the codebase surprises you, that's a documentation
bug — open an issue and tell us where the diagram should have warned
you.
