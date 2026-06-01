# Runbook 006 — Configuration drift from GitOps source of truth

**Tags:** drift, governance, gitops

## Symptom

The live state of a resource (Deployment, ConfigMap, Service, NetworkPolicy) no longer matches the GitOps source of truth (the values currently in the `main` branch of the platform manifests repo). Common observable: a ConfigMap's `data` differs by a key/value, or a Deployment's replicas / image / annotations have diverged. Argo CD or Flux flags this as `OutOfSync`; without those tools, drift may be discovered only when a controller (or an operator) tries to deploy a change that conflicts. Drift on the workload tier is usually benign; drift on platform tier (RBAC, NetworkPolicy, CRDs) is high-risk.

## Diagnosis

The four common roots: (1) **a legitimate manual operator change** during an incident (someone `kubectl edit`ed during a fire) that was never backported to Git; (2) **a controller writing to the resource** — a Deployment's HPA setting `replicas` is normal, not drift; expected drift should be in your GitOps tool's ignore list; (3) **an attacker with cluster write access** modifying a resource to gain footholds — rare but high-impact; (4) **a stale apply by a controller that hasn't been updated** to the new schema. Inspect the resource's `metadata.managedFields` to see which manager last wrote to it; that's almost always conclusive.

## Remediation

For cases 1 and 4 (operator drift, stale controller), the safer action is `rollout_undo` on the affected Deployment if that revision matches Git, OR `page_human` so the operator either back-ports their change to Git or decides to revert. Do NOT auto-reconcile drift on RBAC, CRDs, or NetworkPolicies — these need human review because they're the cluster's trust boundary. For case 2 (controller-managed drift), no action — update the ignore list. For case 3 (attacker), `page_human` immediately; the agent should not race a determined attacker.
