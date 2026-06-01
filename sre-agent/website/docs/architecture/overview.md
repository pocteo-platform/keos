---
id: overview
slug: /architecture/overview
title: Overview
sidebar_position: 1
description: The macro picture of Keos — the LangGraph loop, the safety mesh, the data planes, and one end-to-end request lifecycle.
---

# Architecture

> Goal of this page: an architect who has never seen Keos before should
> understand the whole system in 10 minutes, without reading any code.

## The macro view

Keos is one agent process and a stack of supporting services. The
diagram below shows the runtime topology — services, queues, stores,
and the cluster the agent watches.

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
flowchart TB
    %% External actors
    Op[(Operator)] --SPA--> Caddy
    K[Kubernetes cluster] --kubelet events--> O

    %% Frontend
    Caddy --:80--> Frontend[React SPA]
    Caddy --:8000--> API[FastAPI + LangGraph]
    Caddy --:8000 /ws--> API

    %% Backend services
    subgraph AgentProcess[Agent process — one container]
      API
      O[chaos_observer]
      MS[mode_store]
      Audit[(HMAC audit log)]
    end

    %% External stores
    API <--> Qdrant[(Qdrant — vectors)]
    API <--> Ollama[(Ollama — LLM)]
    API <--> NATS[(NATS JetStream)]
    API <--> OPA[OPA — policy]
    API <--> K8sGPT[K8sGPT MCP]
    API <--> MCP[mcp-server]
    MCP <--> OPA
    MCP <--> K

    %% Control plane
    K --AgentMode CRD--> MS

    classDef container fill:#dbeafe,stroke:#2563eb,color:#1e3a8a
    class Frontend,API,MCP,Qdrant,Ollama,NATS,OPA,K8sGPT,Caddy container
```

Every box is one container. The agent + the MCP server are the only
ones that hold cluster-mutating credentials. All other boxes (Qdrant,
Ollama, NATS, OPA, K8sGPT) are read-only or stateful storage.

## The decision loop

A single incident drives the LangGraph state machine through 8 nodes.

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
stateDiagram-v2
    direction LR
    [*] --> triage
    triage --> retrieve_context: ok
    triage --> escalate: stale_resolved
    retrieve_context --> k8sgpt_analyze
    k8sgpt_analyze --> diagnose
    diagnose --> plan_action: conf >= 0.6
    diagnose --> escalate: conf < 0.6
    plan_action --> policy_gate
    policy_gate --> execute: allow
    policy_gate --> escalate: deny
    execute --> verify: ok
    execute --> escalate: error
    verify --> [*]: healed
    verify --> escalate: not healed
    verify --> [*]: page_human (escalated)
    escalate --> [*]
```

Each node is a pure async function with one job. The conditional edges
keep the routing observable from the audit chain — there's no hidden
state outside `IncidentState`.

## The safety mesh

Three independent guards combine to prevent a misbehaving LLM from
ever mutating the cluster outside its authorized scope:

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
flowchart LR
    subgraph G1[Guard 1 — AgentMode CRD]
      MS[mode_store reads CRD] --reads on every execute--> AM{mode}
      AM --observe-only--> DR[dry-run only]
      AM --frozen--> Den[deny everything]
      AM --active--> P[proceed]
    end

    subgraph G2[Guard 2 — OPA matrix]
      P --> Mat[Rego severity × action]
      Mat --L1/L2--> Exe[allowed]
      Mat --L3/L4--> Esc[escalate]
      Mat --protected ns--> Esc
    end

    subgraph G3[Guard 3 — scoped RBAC]
      Exe --MCP server--> MCPCheck[OPA #2]
      MCPCheck --> KubeAPI[kubectl with scoped SA]
      KubeAPI --> Cluster[(cluster)]
    end

    Cluster -. "every step audit-chained" .-> Audit[(HMAC chain)]
```

- **Guard 1** is hot-flippable. `kubectl patch agentmode default
  -p '{"spec":{"mode":"frozen"}}'` is the kill switch — takes effect
  on the next execute (~1 s).
- **Guard 2** is policy-as-code. The matrix is one file (`policies/agent.rego`)
  with 6 actions × 4 severities; you can read it end to end in 2 minutes.
- **Guard 3** is defense-in-depth. The MCP server runs OPA *again* before
  any kubectl call, so a buggy agent that somehow bypassed its own
  policy_gate still gets blocked.

## The audit chain

Every node entry, every policy decision, every kubectl call writes one
JSON line to `agent/audit.log`. Each line is HMAC-signed and chained:

```
hmac_n = HMAC-SHA256(key, prev_hmac_n-1 || canonical_json(record_n))
```

Tampering with any line breaks every subsequent HMAC. The verifier walks
all rotated log files (`audit.log`, `audit.log.YYYY-MM-DD`, …) and
reports the first invalid index.

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
sequenceDiagram
    participant T as triage
    participant D as diagnose
    participant E as execute
    participant A as audit.log
    T->>A: line 1: hmac1 = HMAC(key, "" || canon_1)
    D->>A: line 2: hmac2 = HMAC(key, hmac1 || canon_2)
    E->>A: line 3: hmac3 = HMAC(key, hmac2 || canon_3)
    Note over A: An attacker who edits line 2<br/>can re-sign line 2 only if<br/>they hold the key.<br/>Without the key, hmac3<br/>no longer matches.
```

The HMAC key rotation procedure breaks the chain on purpose (you can't
re-sign old records with a new key). See [audit-tampering runbook](../runbooks/audit-tampering)
for the forensic procedure when a chain breaks.

## Two consumer lanes

NATS JetStream has two durable consumers — so a slow LLM cycle for a
kubelet event never blocks an operator-initiated use case run.

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
flowchart LR
    subgraph Stream[INFRA stream]
      Subj1["infra.events.*"]
      Subj2["infra.events.usecase.*"]
    end
    Subj1 --pocteo-agent-obs--> ObsLane[OBSERVER consumer]
    Subj2 --pocteo-agent-user--> UserLane[USER consumer]
    ObsLane --> Graph1[LangGraph #1]
    UserLane --> Graph2[LangGraph #2]
```

A 10-minute LLM diagnose call on the OBSERVER lane never starves the
USER lane.

## What lives where

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
flowchart TB
    subgraph repo[sre-agent/]
      agent[agent/<br/>LangGraph, audit, policy gate, FastAPI]
      mcp[mcp_server/<br/>action catalog over MCP]
      frontend[frontend/<br/>React SPA]
      deploy[deploy/helm/pocteo/<br/>Helm chart]
      manifests[manifests/<br/>k3d kubectl apply]
      policies[policies/agent.rego<br/>OPA matrix]
      tests[tests/<br/>pytest + playwright]
      docs[docs/<br/>source markdown]
      website[website/<br/>this Docusaurus site]
    end
```

Other directories: `k3d/` (laptop cluster bootstrap), `scripts/`
(doctor, helpers), `k8sgpt-config/` (K8sGPT config), `chaos/` (Chaos
Mesh recipes for use-case runner).

## One request, end to end

The full lifecycle of a kubelet-event-driven incident:

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
sequenceDiagram
    autonumber
    participant K as kubelet
    participant O as chaos_observer
    participant N as NATS
    participant G as LangGraph
    participant L as Ollama
    participant Q as Qdrant
    participant Op as OPA
    participant M as mcp-server
    participant API as Kube API
    participant A as audit.log

    K->>O: "Container OOMKilled / Pod foo"
    O->>N: publish infra.events.crashloop
    N->>G: deliver to OBSERVER lane
    G->>A: triage start/end (HMAC chained)
    G->>Q: vector_search(rule + output) → top-3
    G->>API: kubectl describe pod foo
    G->>A: retrieve_context end
    G->>L: POST /api/generate (diagnose)
    L-->>G: {summary, root_cause, confidence: 0.85}
    G->>A: diagnose end
    G->>L: POST /api/generate (plan_action)
    L-->>G: {tool: restart_pod, args: {...}}
    G->>A: plan_action end
    G->>Op: evaluate(action, severity, namespace, mode)
    Op-->>G: {allow: true, reasons: []}
    G->>A: policy_gate end
    G->>M: call_tool(restart_pod, args)
    M->>Op: re-evaluate
    Op-->>M: allow
    M->>API: kubectl delete pod foo
    API-->>M: ok
    M->>A: execute end
    G->>API: kubectl get pod -l selector (poll 30s)
    API-->>G: 3/3 Ready
    G->>A: verify end (healed: true)
    G->>N: ack
```

## Next

- [The loop](the-loop) — node-by-node deep-dive
- [Safety model](safety-model) — the three guards in detail
- [Audit chain](audit-chain) — HMAC verification + forensic recovery
