# Drift storm — too many drift events at once

> **Symptom.** The Settings → Drift tab shows hundreds of recent
> `infra.events.drift.*` events. The Reliability page's GitOps drift
> tile is in the dozens. The Use Cases / Pipeline page is animating
> faster than an operator can read.

The Phase 15 drift detector is firing on more resources than the
operator can review in a reasonable amount of time. Three usual
causes:

1. **Bulk SoT change without a deploy** — operator edited `manifests/`
   but didn't `kubectl apply -f` first, so the live cluster is "behind"
   by every change at once.
2. **Controller writeback you didn't whitelist** — a CRD controller
   (Argo Rollouts, Istio Sidecar Injector, Cert-Manager Injector) is
   mutating fields the comparator isn't ignoring. Every reconcile cycle
   produces an event.
3. **Time-window event flood** — the detector just started after a
   long downtime. It catches up on every divergence in one tick.

---

## Triage (5 min)

```bash
# Confirm the storm is drift-specific (not all-events)
curl -s http://localhost:8000/api/audit?limit=200 \
  | jq '.items[].payload.rule' \
  | grep -ic drift

# Look at WHICH resources are drifting most. If it's one kind
# (e.g. all are ConfigMap), suspect a controller writeback.
docker logs shp-api 2>&1 | grep -E 'drift: published.*drift event' | tail -20

# Inspect the deltas on a recent event — paths shows what fields
# are diverging. A controller-writeback storm usually shows ONE
# repeating path across every event (e.g. spec.template.metadata.annotations.X).
curl -s http://localhost:8000/api/incidents?limit=20 \
  | jq '.items[] | select(.rule | startswith("GitOps drift")) | {kind: .pod, rule, ts}'
```

If the same `path` appears across every drift event → **cause #2
(controller writeback)**. Skip to "Resolution → add an ignore-fields
annotation".

If the events cover many different kinds + paths → **cause #1
(bulk SoT change)**. Apply the changes, then the storm clears on
the next detector tick.

If the storm started right after `docker compose up` or `kubectl
rollout restart deploy/api` → **cause #3 (cold-start catchup)**. Wait
~10 minutes; it'll quiet down once every drifted resource has been
detected once and entered the per-target dedup cooldown.

---

## Containment

The detector's tick + dedup window are independently disablable:

```bash
# Option A — disable the detector entirely (zero overhead while debugging)
kubectl set env deploy/keos-api DRIFT_SOURCE_TYPE=none -n keos
# OR docker-compose laptop:
docker compose stop api
sed -i 's/DRIFT_SOURCE_TYPE=manifests/DRIFT_SOURCE_TYPE=none/' .env.local
docker compose start api

# Option B — raise the dedup window from 600 s → 3600 s to throttle
# without disabling
kubectl set env deploy/keos-api DRIFT_DEDUP_WINDOW_S=3600 -n keos
```

If the agent's mode is `active` AND the matrix allows `revert_drift`
at the events' severity, the agent will start auto-reverting under
the storm. **Flip to observe-only** before debugging if you don't
want that:

```bash
kubectl patch agentmode default \
  --patch-file manifests/patches/mode-observe.yaml
```

---

## Resolution

### Cause #1 — bulk SoT change

```bash
# Apply every manifest in the SoT directory. The detector will
# clear the storm on its next tick.
kubectl apply -f manifests/

# Verify the next detector tick is quiet
docker logs --since 3m shp-api 2>&1 | grep 'drift: published'
# Should be 0 events.
```

### Cause #2 — controller writeback

Identify the offending path from triage step 3 (the repeating field
across events).

**Per-manifest fix (preferred — narrow scope):**

```yaml
# In manifests/<the-resource>.yaml, add the annotation:
metadata:
  annotations:
    pocteo.io/drift-ignore-fields: "spec.template.metadata.annotations.injected-by"
```

Multiple paths comma-separated.

**Global fix (if many resources affected):**

Edit `agent/drift/comparator.py`'s `DEFAULT_WHITELIST_PATHS` — append
a tuple-form path and ship a release. This requires a code change +
test + deploy, so prefer per-manifest unless the controller mutates
hundreds of resources.

### Cause #3 — cold-start catchup

Do nothing. The dedup window kicks in after the first event per
target; the storm decays exponentially over the next 10–15 minutes.
If you can't wait: temporarily set `DRIFT_DEDUP_WINDOW_S=86400` so
each target is silenced for 24 h, then revert.

---

## Postmortem template

```markdown
# Drift storm — YYYY-MM-DD

## Trigger
[bulk SoT change | controller writeback | cold-start catchup]
First detector tick after the storm started: [HH:MM UTC]
Peak event rate: [N/min]
Resolved at: [HH:MM UTC]

## Detection
[How did you notice — operator alert, page, dashboard chart, etc.]
Time from start → detection: [N min]

## Containment
[What you flipped — detector off / dedup window raised / mode frozen]
Time from detection → containment: [N min]

## Resolution
[Which cause + which fix — kubectl apply / annotation / wait]
Time from containment → resolution: [N min]

## Action items
- [ ] Update <controller> writeback annotation in <files>
- [ ] Add alert rule for `drift_events_total > 50 in 5m`
- [ ] (optional) Document the controller's writeback behaviour in
      docs/operations/gitops-drift.md
```

---

## See also

- [Phase 15 — GitOps drift detection](../../website/docs/architecture/gitops-drift.md)
  — architecture deep-dive
- [agent/runbooks/012-drift.md](../../agent/runbooks/012-drift.md) — the
  RAG-indexed diagnostic runbook (read by the diagnose LLM, not by
  humans)
- [Settings → Drift tab](../UI_GUIDE.md) — operator-facing event list
