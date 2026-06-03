# Runbook 012 — GitOps drift

**Tags:** drift, gitops, declarative, reconciliation

## Symptom

The drift detector emits `infra.events.drift.<Kind>` when a live K8s resource diverges from its source-of-truth manifest. The event carries `output_fields.drift.paths` (the list of dotted paths that drifted), `drift.source` (the SoT file location), and `drift.deltas` (the first 20 path / kind diffs).

Drift can come from any of three causes:

1. **An operator ran `kubectl edit` or `kubectl scale` against the live cluster.** A debug action that nobody committed back to Git. Visible in `kubectl.kubernetes.io/last-applied-configuration` annotation drift.
2. **A controller wrote a spec field nobody whitelisted.** HPA on `spec.replicas`, an admission webhook injecting a sidecar, a service mesh stamping `spec.template.metadata.annotations`. These should be in the field whitelist — if you see them, the whitelist needs an entry.
3. **The Git SoT was force-pushed and the detector hasn't pulled.** The cluster looks "drifted" because the local SoT copy is stale. Not real drift.

## Diagnosis

Cross-reference three signals before choosing an action:

* **Drift paths.** `output_fields.drift.paths` tells you exactly what fields differ. If they're all under `metadata.annotations` (especially the `kubectl.kubernetes.io/restartedAt` annotation), an operator probably ran a rollout-restart without committing.
* **Live state ownership.** Does the resource have a `managedFields` entry from a controller other than `kubectl`? If `Deployment.spec.replicas` is the only drift and `metadata.managedFields[*].manager` includes `horizontal-pod-autoscaler`, HPA owns it; the whitelist should suppress this but the per-resource override hasn't been set. Recommend `page_human` to fix the whitelist, not `revert_drift`.
* **Severity.** The detector tags the event with a per-kind priority (Deployment → high, ConfigMap → medium, RBAC → low). Cross-reference against the OPA matrix — `revert_drift` is L3 at low severity (operator approval required) and L1 at high (autonomous allowed in active mode).

## Remediation

**Preferred path:** `revert_drift` with the args copied verbatim from the event payload:

```json
{
  "tool": "revert_drift",
  "args": {
    "kind": "<output_fields.k8s.kind>",
    "namespace": "<output_fields.k8s.ns.name>",
    "name": "<output_fields.k8s.name>",
    "sot_source": "<output_fields.drift.source>"
  },
  "scope": "ns",
  "reversible": true,
  "rationale": "Re-applying the source-of-truth manifest to revert unauthorised live changes."
}
```

The tool reads exactly the document at `sot_source` and runs `kubectl apply` against it. It refuses to apply if the document's `kind` / `name` don't match the requested target — defensive guard against a stale audit-driven retry pointing at the wrong manifest.

**Page-human paths:**

* The drift looks intentional (e.g., a `kubectl.kubernetes.io/restartedAt` annotation suggests someone bounced the deploy; reverting would undo their work).
* The namespace is protected (kube-system, kube-public).
* The drift paths include resource limits / requests — revert would risk reintroducing the leak the operator's manual edit was suppressing.
* The SoT manifest doesn't exist yet (a new resource that hasn't been GitOps'd into the repo).

Never `restart_pod` or `rollout_undo` for a drift event — those address symptoms, not cause. The drift will return at the next reconcile.

## Defensive options

* **Per-manifest ignore:** add `metadata.annotations.pocteo.io/drift-ignore-fields: "spec.replicas,spec.template.spec.containers[0].resources"` to the SoT YAML. The comparator will strip those paths before diffing for THIS manifest only.
* **Global whitelist:** the comparator's `DEFAULT_WHITELIST_PATHS` covers the common controller-owned fields. If a new controller appears (Istio, Linkerd, …) and writes spec fields, add its paths there and rebuild the agent.
* **Kill-switch:** `DRIFT_SOURCE_TYPE=none` disables the detector at deploy time without code change. The rest of the agent keeps working.
