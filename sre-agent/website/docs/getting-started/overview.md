---
id: overview
slug: /getting-started/overview
title: Overview
sidebar_position: 1
description: What you're installing, what runs where, and what the resource budget looks like.
---

# Overview

Before you run `make install`, here's a 90-second tour of what lands on
your machine.

## What you get

Keos is **a single agent process plus a stack of stateful services**.
Everything runs in Docker (laptop demo) or in-cluster Kubernetes
(Helm path); the topology is identical.

```text title="Mermaid diagram (paste into https://mermaid.live to render)"
flowchart LR
    subgraph Edge[Cluster events]
      K[kubelet] -- "OOMKill / BackOff / FailedScheduling" --> O[chaos_observer]
    end
    subgraph Bus[Event bus]
      O --> N[NATS JetStream]
    end
    subgraph Brain[Agent — LangGraph]
      N --> T[triage]
      T --> R[retrieve_context]
      R --> K8[k8sgpt_analyze]
      K8 --> D[diagnose LLM]
      D --> P[plan_action LLM]
      P --> G[policy_gate]
      G -- "allow" --> E[execute via MCP]
      G -- "deny" --> Esc[escalate]
      E --> V[verify]
      V -- "healed" --> END((END))
      V -- "not healed" --> Esc
      Esc --> END
    end
    subgraph Stores[Stateful stores]
      Q[Qdrant — RAG runbooks]
      Ol[Ollama — LLM + embed]
      Op[OPA — policy engine]
    end
    R --> Q
    R --> Ol
    D --> Ol
    P --> Ol
    G --> Op
    E -. "audit-chained" .-> A[(HMAC audit log)]
```

Each box runs as a container:

| Component | Image | Purpose |
|---|---|---|
| **agent** (api) | `pocteo-api:1.0.0-rc.1` | LangGraph state machine + FastAPI + WebSocket bus |
| **mcp-server** | `pocteo-mcp-server:1.0.0-rc.1` | Action catalog (restart_pod, rollout_undo, …) gated through OPA |
| **frontend** | `pocteo-frontend:1.0.0-rc.1` | React/Vite SPA — bilingual EN/FR |
| **caddy** | `caddy:2.8-alpine` | Reverse proxy on `:80` |
| **ollama** | `ollama/ollama:0.4.7` | Local LLM (qwen2.5:3b) + embed model (nomic-embed-text) |
| **qdrant** | `qdrant/qdrant:v1.12.4` | Vector DB for retrieval-augmented runbook context |
| **nats** | `nats:2.10.22-alpine` | JetStream event bus |
| **opa** | `openpolicyagent/opa:0.69.0` | Policy engine — every action passes through |
| **k8sgpt** | `ghcr.io/k8sgpt-ai/k8sgpt:v0.4.33` | Cluster-signal enrichment (read-only) |

Plus a k3d-managed local cluster (laptop demo) or your real cluster (Helm path).

## The loop

Every incident follows the same eight-step graph:

| Phase | Node | What happens |
|---|---|---|
| **Observe** | `triage` | Validate the event; short-circuit if stale (>10 min) and already healed |
| | `retrieve_context` | Pull the top-3 RAG runbooks + `kubectl describe` for the pod |
| | `k8sgpt_analyze` | Run K8sGPT's deterministic analyzers; cap to relevant findings |
| **Predict** | `diagnose` | LLM call — produce structured `{summary, root_cause, confidence, evidence}` |
| | `plan_action` | LLM call — choose ONE tool from the catalog with args |
| **Heal** | `policy_gate` | OPA: severity × action × namespace check (fails closed on OPA unreachable) |
| | `execute` | kubectl via the MCP server; ALL actions audit-chained |
| **Learn** | `verify` | Poll cluster state for ≤30 s; mark healed / escalate |

A bad event → low confidence → routes to `escalate` early. A denied
action → `escalate` immediately. A failed execute → `escalate`. A
heal-but-pod-not-ready → `escalate`. Every terminal state is captured
in the audit chain.

## Resource budget

| Resource | Laptop demo | Cluster (single node) | Notes |
|---|---|---|---|
| Disk | ~3 GB | ~3 GB + model storage | Models go in a named volume (`shp_ollama_models`) so `make down` keeps them |
| RAM | 6 GB free | 8 GB recommended | LLM is ~2 GB resident; Qdrant + NATS + frontend add ~1 GB |
| CPU | Any modern x86 | Any conformant arch | First scenario: ~4-5 min (LLM cold-load). Subsequent: ~30-60 s |
| GPU | Optional | NVIDIA Container Toolkit | Cuts each LLM call from ~60 s → ~2 s; see [Jetson](../platforms/jetson) |

## What's already configured

Out of the box, Keos ships with:

- 8 SRE-themed runbooks indexed in Qdrant (memory leak, crashloop, image
  pull failure, node pressure, rollout regression, config drift, traffic
  surge, failed scheduling)
- A 6-action catalog (`restart_pod`, `rollout_undo`, `scale_deployment`,
  `cordon_node`, `drain_node`, `page_human`) — gated by the Rego matrix
- A library of 4 use cases the SPA can run on demand (memory leak,
  crashloop recovery, node pressure, drain low-severity)
- Three intentionally-broken demo workloads to verify the loop end-to-end
  (`nginx-demo` healthy, `busybox-bad` crashlooping, `broken-image` ImagePullBackOff)

Next: [Installation](installation).
