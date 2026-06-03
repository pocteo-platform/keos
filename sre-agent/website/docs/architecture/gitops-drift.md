---
sidebar_position: 7
title: GitOps drift detection (Phase 15)
---

# GitOps drift detection

Phase 15 broadens the agent's event surface. Until Phase 14, every
incident the agent reacted to started with a kubelet event — something
had already gone wrong on a Pod. Phase 15 adds a second event source:
**unauthorised divergence between the live cluster and its declarative
source-of-truth**. A `kubectl edit deploy` that wasn't supposed to
happen, a leftover from a controller writeback, a stale GitOps repo —
all of them produce `infra.events.drift.<kind>` events that flow
through the same LangGraph the reactive events do.

This closes the **Observe** half of the loop for changes that never
manifest as a crash: drift is silent failure that an operator may not
notice for hours. The agent's reactive layer only ever wakes on
symptoms (OOMKilled, CrashLoopBackOff); the drift layer wakes on cause.

## What ships in Phase 15

Three new modules under `agent/drift/`:

| File | Responsibility |
|---|---|
| `sources.py` | `SourceOfTruth` protocol + `ManifestsSource` filesystem provider. Future ArgoCD / Flux / raw-Git providers slot in here without touching the rest. |
| `comparator.py` | Pure-function deep diff between a SoT manifest and a live cluster object, with a comprehensive whitelist for server- and controller-owned fields. |
| `detector.py` | Background asyncio task. Polls the SoT every `DRIFT_TICK_INTERVAL_S` (default 120 s), fetches live state per target, diffs, and publishes one NATS event per drifted resource. Dedup window 600 s. |

Plus one new action tool — `revert_drift` — and a new runbook
(`012-drift.md`) that the diagnose LLM retrieves through the existing
RAG path.

## Why a separate detector instead of "reuse Argo CD"

Three reasons:

1. **Edge clusters don't always run a GitOps controller.** Argo CD on a
   Jetson Orin Nano with 8 GB RAM is competing for resources the agent
   needs for inference. Phase 15's `ManifestsSource` reads a
   bind-mounted directory of YAML files — zero extra cluster overhead.
2. **The agent needs to react to drift, not just report it.** Argo CD
   detects and (optionally) reverts. The agent diagnoses through the
   LLM, picks between `revert_drift` and `page_human` based on
   severity + the operator's policy, and audit-trails everything
   through the same HMAC chain as every other action.
3. **Future providers plug in cleanly.** The `SourceOfTruth` Protocol
   is small (one method, `manifests()`). When a customer brings their
   own Argo CD / Flux deployment, the agent reads `Application` /
   `Kustomization` CRs through a new provider class — same comparator,
   same detector, same policy matrix.

## The comparator's whitelist

The hard part isn't the diff — it's the whitelist of fields the diff
must ignore so the agent doesn't drown in false positives. Two
categories:

### Server-controlled fields

Written by the K8s API server on every reconcile. Including them as
drift would flood the operator with meaningless events:

- `metadata.uid`, `metadata.resourceVersion`, `metadata.generation`
- `metadata.creationTimestamp`, `metadata.deletionTimestamp`
- `metadata.managedFields`, `metadata.selfLink`, `metadata.finalizers`
- `status.*` (server-owned end-to-end)

### Controller-owned spec fields

Written by other controllers — treating them as drift causes false
positives the operator can't fix without disabling the controller:

- `spec.replicas` — owned by HPA (operators who pin replicas in SoT and
  *want* drift on them override via the per-manifest ignore annotation)
- `spec.clusterIP`, `spec.clusterIPs`, `spec.ipFamilies` — server-assigned
- `spec.ports[*].nodePort` — server-assigned when unset
- `secrets[*]` — auto-mounted by the ServiceAccount controller

### Operator escape hatches

Two operator-facing knobs, both documented in `runbooks/012-drift.md`:

| Knob | Where | What it does |
|---|---|---|
| `pocteo.io/drift-ignore-fields` annotation | per manifest in the SoT | Comma-separated dotted paths the comparator skips for that manifest only. |
| `DRIFT_SOURCE_TYPE=none` | env var on the agent container | Disables the detector entirely without a rebuild. |

## The data flow

```text title="Mermaid (paste into https://mermaid.live)"
flowchart LR
    subgraph SoT[Source of truth]
      Dir[manifests/<br/>YAML files]
    end
    subgraph Agent[Drift detector tick — every 120 s]
      Walk[ManifestsSource.manifests<br/>iterator]
      Live[_LiveLookup.fetch<br/>kubernetes-client typed API]
      Cmp[compare_resources<br/>strip + diff]
      Pub[NATS publish<br/>infra.events.drift.kind]
    end
    subgraph Bus[Event bus]
      N[(NATS JetStream)]
    end
    subgraph Graph[Existing LangGraph]
      TR[triage] --> RC[retrieve_context]
      RC --> KG[k8sgpt_analyze]
      KG --> EB[enrich_with_baselines]
      EB --> D[diagnose]
      D --> PA[plan_action]
      PA --> PG[policy_gate]
      PG --> EX[execute<br/>revert_drift OR<br/>page_human]
    end
    Dir --> Walk
    Walk --> Live
    Live --> Cmp
    Cmp --drifted--> Pub
    Cmp --in-sync--> Skip[no event]
    Pub --> N
    N --> TR
```

Two important properties:

1. **Sub-event severity.** Drift events fire at one severity step
   **lower** than the reactive equivalent. A drifted Deployment is
   "high" (where a CrashLoopBackOff Deployment is "critical"), a
   drifted ConfigMap is "medium" — so OPA's matrix is more conservative
   for autonomous reverts.
2. **Soft-fail everywhere.** SoT missing → log + skip tick. K8s
   unreachable → log + skip tick. NATS down → log + skip tick. The
   detector never crashes the agent process; the worst case is the
   tick stays a no-op until the dependency recovers.

## The `revert_drift` action

When `diagnose` plus `plan_action` decide a drift event should be
auto-reverted, the LLM emits `tool: revert_drift` with args:

```json
{
  "kind": "Deployment",
  "namespace": "workloads",
  "name": "nginx-demo",
  "sot_source": "/manifests/demo-workloads.yaml#1"
}
```

The MCP server then:

1. Re-evaluates OPA (defense-in-depth — same OPA call the agent already
   made in `policy_gate`).
2. Re-reads the SoT manifest from `sot_source` (path or `path#doc_index`).
3. Verifies the manifest's `kind` + `metadata.name` match the
   requested target — refuses to apply otherwise. This is the
   defensive guard against a stale audit replay where the manifest
   file has been edited between event and action.
4. Writes the single doc to a temp file and `kubectl apply -f` it
   under the agent's scoped ServiceAccount.

The action is treated as **reversible** in the OPA matrix because
applying a SoT manifest is semantically idempotent — re-running it
against an already-in-sync resource is a no-op.

## OPA matrix entry for `revert_drift`

```text
revert_drift:
  low:      L3   # drift on a Role / RoleBinding — operator should look first
  medium:   L2   # drift on ConfigMap / Service — gated on active mode
  high:     L1   # drift on Deployment / StatefulSet — auto-revert ok
  critical: L1   # never reached today; reserved for future severity boosts
```

The `L3` at low severity is intentional: a drifted RBAC object is the
kind of thing where an operator usually needs to know *why* before the
agent re-applies the manifest, so we escalate by default.

## Operator workflow

The bind mount in `docker-compose.yml` (and the equivalent ConfigMap
or PVC in the Helm chart) makes the operator workflow a one-liner:

```bash
# Operator edits the SoT
$EDITOR /path/to/manifests/demo-workloads.yaml

# The next detector tick (≤ 120 s) sees the new SoT
# and publishes drift events for any resource whose live state
# differs from the new manifest. The agent then:
#   - active mode    → reverts (or scales the diff up to a page_human if L3)
#   - observe-only   → dry-runs + audit-trails what it would have done
#   - frozen         → does nothing (kill-switch)
```

For the laptop demo, point a manifest at the wrong image and watch
the detector wake up on the next tick:

```bash
# 1. Pollute the live state
kubectl set image deploy/nginx-demo -n workloads nginx=nginx:1.99-broken

# 2. Wait up to 120 s for the next drift tick.
#    Audit log will show:
#      infra.events.drift.Deployment
#      pipeline.triage.start
#      pipeline.diagnose.end (root_cause: image drift vs SoT)
#      pipeline.plan_action.end (tool: revert_drift)
#      pipeline.execute.end (ok: true)
#    The image rolls back to nginx:1.27-alpine on its own.
```

## What's deliberately out of scope (for now)

- **Argo CD / Flux providers.** The `SourceOfTruth` protocol is ready
  for them; no other code changes needed. Punted to a future phase so
  Phase 15 ships v1 with one solid provider rather than three fragile
  ones.
- **Smart list merging.** The comparator compares list-of-objects
  positionally (`spec.template.spec.containers[0]` vs same). K8s'
  strategic-merge-patch is smarter (it merges by `name` on container
  lists, by `containerPort` on port lists, etc.). Operators who hit
  false positives on container reordering use the ignore annotation;
  v2 will model strategic-merge.
- **Drift on cluster-scoped CRs.** Today the detector handles
  Namespace + ClusterRole + ClusterRoleBinding. CRDs and other
  cluster-scoped kinds are dispatched through the same `_fetch_typed`
  switch — extending it is a one-liner per new kind.

## Verification

After `make up`:

```bash
# Confirm the detector started
docker logs shp-api 2>&1 | grep "drift:"
# Expected:
# drift: detector started (source=manifests tick=120s dedup=600s)

# Inject drift + watch the agent react
kubectl set image deploy/nginx-demo -n workloads nginx=nginx:1.99-broken
sleep 130
docker logs shp-api 2>&1 | grep "drift:" | tail
# Expected:
# drift: published 1 drift event(s)

# Watch the pipeline animate in the SPA at /pipeline/live
```

## Related

- [Predictive forecasting](forecasting) — Phase 14's other proactive
  event source. Forecasting fires events about *what will go wrong*;
  drift fires events about *what already silently went wrong*.
- [ML baselines](ml-baselines) — Phase 13. The `enrich_with_baselines`
  node sits between `k8sgpt_analyze` and `diagnose` for drift events
  too, but typically yields no signal (drift is a config issue, not a
  metric issue).
- [Policy matrix](../configuration/policy-matrix) — full Rego matrix
  including the Phase 15 `revert_drift` entry.
- [Runbook 012 — Drift](../runbooks/) — diagnosis + remediation
  guidance the diagnose LLM retrieves via RAG.
