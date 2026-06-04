# Edge install, node attachment, retention, and backups

> **Status.** Design document. Phase A (cluster-context endpoint + dashboard
> card) is shipped in this same commit; Phases B–G are sequenced below.
> This is the architect's-eye-view of what "an SRE agent that ships
> *inside* every edge cluster" actually requires past the demo stage.

The Keos SRE Agent's design contract — *autonomous, resilient, secure,
smart, learning, edge-deployed* — has three operational gaps the existing
docs don't address head-on:

1. **Edge install** — how does the agent land on an edge cluster (Jetson,
   Talos, k3s on a Pi cluster) and discover what it's working with?
2. **Node attachment** — can the operator pin the agent to a specific
   node (the one with the GPU, the one with the local SSD, the one
   they trust)?
3. **Data lifecycle** — how does the operator manage retention windows
   and take backups *without writing YAML*?

This doc proposes the design across all three.

---

## §1 Edge install — node-aware bootstrap

### Where we are today

`make demo-ui` works. `helm install pocteo deploy/helm/pocteo` works
against a generic K8s cluster. **What doesn't work yet:**

- The agent assumes someone hand-wrote `values.yaml` correctly.
- No detection of which K8s distro the cluster is (k3s / Talos / RKE2 / kubeadm).
- No detection of which operators are already installed (Argo CD,
  Flux, Chaos Mesh, Prometheus, cert-manager).
- No way to pin the agent to a specific node — it lands wherever the
  scheduler picks.

### What competitors do

| Product | Onboarding style | What they get right |
|---|---|---|
| **Komodor** | Auto-detects kube-state-metrics + existing controllers, offers to install missing pieces. | Detection-first; no YAML on day 1. |
| **Lens IDE** | Discovers Helm releases, cert-manager, Argo CD across every cluster on first connect. | Cluster context is a first-class surface. |
| **Robusta** | `robusta gen-config` — interactive CLI that auto-detects Slack workspace from env if present. | Wizard-first install, no required YAML. |
| **k0sctl / k3sup / talosctl** | Declarative cluster spec with node roles ("control-plane", "worker"). | Treats nodes as named, role-tagged entities. |
| **CNPG / postgres-operator** | CR with `nodeAffinity` field; `topologySpreadConstraints` for HA. | Node placement is a CR field, not a kubectl ritual. |

We want **Komodor's auto-detect + Robusta's wizard + k0sctl's node roles**.

### Proposed design

#### A. Cluster-context probe (backend)

New endpoint `GET /api/cluster/context` returns everything the agent
discovered at boot:

```json
{
  "distro": "k3s | k3d | talos | rke2 | kubeadm | gke | eks | aks | unknown",
  "runtime": "containerd | crio | docker | unknown",
  "kubernetes_version": "v1.30.4+k3s1",
  "nodes": [
    {
      "name": "k3d-poc-server-0",
      "role": "control-plane",
      "ready": true,
      "is_agent_home": false,
      "cpu_capacity_m": 8000,
      "memory_capacity_bytes": 16000000000,
      "gpu_capacity": null,
      "labels": { "kubernetes.io/arch": "amd64", "kubernetes.io/os": "linux" },
      "taints": []
    }
  ],
  "detected_operators": {
    "argocd": false,
    "flux": false,
    "chaos_mesh": true,
    "prometheus": true,
    "cert_manager": false,
    "kube_state_metrics": true
  },
  "agent_self": {
    "namespace": "pocteo",
    "pod": "pocteo-api-abc",
    "node": "k3d-poc-server-0"
  },
  "warnings": [
    "Argo CD not detected → GitOps drift will need DRIFT_SOURCE_TYPE=manifests"
  ]
}
```

Detection logic per field:

- `distro` — order: check Node annotations for `k3s.io/...`, check `kube-system` Deployment names (`talos-cluster-discovery`, `rke2-coredns`, `eks-coredns`), default `unknown`.
- `runtime` — read the kubelet's `nodeInfo.containerRuntimeVersion` (it's `containerd://1.7.x` or `cri-o://x`).
- `nodes` — `list_node()` + filter labels. `gpu_capacity` reads `status.capacity["nvidia.com/gpu"]`.
- `detected_operators` — well-known CRD probes (`argocd.argoproj.io`, `chaos-mesh.org/v1alpha1`, `monitoring.coreos.com`). Best-effort; absence = false.
- `agent_self` — downward API. The agent's Deployment template gets:
  ```yaml
  env:
    - name: KEOS_POD_NAMESPACE
      valueFrom: { fieldRef: { fieldPath: metadata.namespace } }
    - name: KEOS_POD_NAME
      valueFrom: { fieldRef: { fieldPath: metadata.name } }
    - name: KEOS_NODE_NAME
      valueFrom: { fieldRef: { fieldPath: spec.nodeName } }
  ```

#### B. Node attachment (Helm + CR)

New Helm values:

```yaml
agent:
  attachment:
    # "soft" — prefer this node, fall back if unschedulable.
    # "hard" — only this node; scheduling fails otherwise.
    # "none" — no preference (default).
    mode: none
    # The node's Kubernetes name. Leave empty for `mode: none`.
    nodeName: ""
    # Or use an arbitrary label matcher (operator-supplied):
    nodeSelector: {}
  # The chart can label the chosen node at install time so we can
  # find it later. Useful for HA rollovers across cluster rebuilds.
  bootstrapNodeLabel:
    enabled: true
    key: keos.pocteo/sre-home
    value: "true"
```

A pre-install Helm hook (`templates/_bootstrap-node-label.yaml`) labels
the chosen node before the Deployment lands:

```yaml
{{- if and .Values.agent.attachment.nodeName .Values.agent.bootstrapNodeLabel.enabled }}
apiVersion: batch/v1
kind: Job
metadata:
  annotations: { "helm.sh/hook": pre-install }
  ...
  containers:
    - name: label-node
      image: bitnami/kubectl:1.30
      command:
        - kubectl
        - label
        - node
        - "{{ .Values.agent.attachment.nodeName }}"
        - "{{ .Values.agent.bootstrapNodeLabel.key }}={{ .Values.agent.bootstrapNodeLabel.value }}"
        - --overwrite
{{- end }}
```

Then the Deployment reads the label:

```yaml
{{- if eq .Values.agent.attachment.mode "hard" }}
nodeSelector:
  {{ .Values.agent.bootstrapNodeLabel.key }}: {{ .Values.agent.bootstrapNodeLabel.value | quote }}
{{- else if eq .Values.agent.attachment.mode "soft" }}
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: {{ .Values.agent.bootstrapNodeLabel.key }}
              operator: In
              values: [{{ .Values.agent.bootstrapNodeLabel.value | quote }}]
{{- end }}
```

#### C. First-time-setup wizard (SPA)

On first login (`GET /api/setup/state` → `not_completed`), the SPA
redirects to `/setup/welcome` instead of `/dashboard`:

| Step | What the operator sees |
|---|---|
| **Welcome** | One-paragraph "what Keos will do" + the agent's home node identifier (from downward API). |
| **Cluster context** | The probe output rendered as a labelled card grid — distro, runtime, K8s version, node count, total resources, detected operators. "Looks right? [Confirm] / [Re-detect]". |
| **Node selection** | Interactive node table with metrics + the agent's current node highlighted. Lets the operator pick a different node (writes `agent.attachment.nodeName` to a generated values overlay). |
| **Integrations** | One toggle per detected operator: "Use Argo CD as the SoT for drift detection?" / "Start baseline learning against Prometheus?". Each toggle prints the matching `values.yaml` patch. |
| **Agent mode** | Three big radio buttons: `observe-only` (default) / `active` / `frozen`. Each with a one-line description. |
| **Notifications** | Slack webhook + generic webhook URLs (skip-able). |
| **Review & apply** | Renders a `helm upgrade --values overlay.yaml` command the operator copies. Marks setup complete via `POST /api/setup/complete`. |

Persistent surface after the wizard finishes: `Settings → Cluster
context` tab shows the probe result + lets the operator re-run it
(e.g. after adding a new node to the cluster).

---

## §2 Retention controls in the UI

### Where we are today

Three env vars + a background pruner:

| Env var | Default | What it limits |
|---|---|---|
| `AUDIT_LOG_RETENTION_DAYS` | 30 | `audit.log.YYYY-MM-DD` rotated files |
| `INCIDENT_RETENTION_DAYS` | 90 | `agent/incidents/*.json` snapshots |
| `TRACE_RETENTION_DAYS` | 30 | `agent/traces/*.jsonl` per-incident trace files |
| `RETENTION_SWEEP_INTERVAL_S` | 3600 | How often the pruner runs |

The values are baked into the Deployment env at install time. Changing
them requires a `helm upgrade` + pod restart. **An operator can't tune
this from the UI.**

### What competitors do

| Product | Retention UI |
|---|---|
| **Datadog** | Account-level dropdown, applied vendor-side. |
| **Komodor** | Account retention policy in their SaaS UI. |
| **Grafana / Loki** | Per-tenant YAML; no operator UI. |
| **Velero** | Per-`Schedule` CR TTL. |
| **PostgreSQL CNPG** | Storage-level retention only (PVC reclaim policy). |

Most products either hide retention behind their SaaS account UI (no
help to us — we're shipping software you self-host) or push it down
to YAML (the gap we're trying to close). Velero is the closest fit:
TTL on the backup spec, no separate UI surface.

### Proposed design

#### Settings → Data Lifecycle tab

Three sliders (with i18n labels), a disk-usage panel, and a sweep status.

```
┌──────────────────────────────────────────────────────────┐
│  RETENTION SETTINGS                                       │
│                                                           │
│  Audit log         ⊙────────────●─────── 30 days          │
│                    HMAC-chained record of every action.   │
│                                                           │
│  Incidents         ⊙─────────●────────── 90 days          │
│                    Per-incident state + outcome JSON.     │
│                                                           │
│  Pipeline traces   ⊙──────●───────────── 30 days          │
│                    Per-node start/end records.            │
│                                                           │
│                                              [Save]       │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│  DISK USAGE                       Last sweep: 12 min ago  │
│                                                           │
│  Audit log         142 MB   487 daily files               │
│  Incidents          18 MB   1,234 snapshots               │
│  Traces             34 MB   1,234 trace files             │
│  Calibration        2 MB    1 SQLite DB                   │
│  Baselines          5 MB    1 SQLite DB                   │
│  ──────────────────────                                   │
│  Total             201 MB                                 │
│                                                           │
│  Next pruner sweep in 48 minutes.                         │
└──────────────────────────────────────────────────────────┘
```

#### API surface

```
GET  /api/retention
  → { days: {audit, incidents, traces},
      disk_usage: { audit_bytes, incidents_bytes, traces_bytes,
                    calibration_bytes, baselines_bytes },
      last_sweep_at: "...",
      next_sweep_at: "...",
      files: { audit_count, incident_count, trace_count } }

PUT  /api/retention { days: {audit, incidents, traces} }
  → 200 + new state
  → Live-applies; no pod restart needed.
```

#### Backend implementation

The pruner already reads its config from `settings`. We add a small
overlay store at `/data/retention.json` that the PUT writes to + the
pruner reads on each tick. On agent boot the overlay (if present) wins
over the env vars; this lets the operator's UI choice survive Helm
re-rolls.

```python
# agent/retention/store.py
class RetentionStore:
    def __init__(self, path: Path): ...
    def get(self) -> RetentionConfig: ...   # overlay-or-default
    def put(self, cfg: RetentionConfig) -> None: ...
```

The pruner reads `store.get()` on each iteration so live edits take
effect at the next sweep (≤ 1 hour, configurable).

---

## §3 Backup snapshots in the UI

### Where we are today

- `scripts/backup_restore_drill.py` proves the round-trip works
  end-to-end (audit chain re-verified, calibration override preserved,
  use cases JSON survives, etc.).
- `templates/backup-cronjob.yaml` ships a Velero `Schedule` CR
  gated on `backup.velero.enabled: false`.
- **No operator UI.** Restore requires shell access + running the drill
  script with the right args. That's a recovery-time SLA we shouldn't
  accept.

### What competitors do

| Product | Backup model | UI surface |
|---|---|---|
| **Velero** | Backup + Schedule CRs; TTL per backup; arbitrary storage backends. | First-class CRDs + community Velero UI plugin. |
| **CNPG / postgres-operator** | Backup CRs with `target` field (S3 / Azure / GCS). | List view in their operator UI. |
| **Vault Enterprise** | First-class snapshot + restore API. | UI for take-snapshot + restore-from-snapshot. |
| **etcd-cluster-operator** | `Backup` CR per snapshot. | Crossplane-style list view. |
| **Komodor** | None — they don't snapshot operator state. | N/A. |

We want **Vault's snapshot API + Velero's storage flexibility + CNPG's
list-and-restore UI**.

### Proposed design

#### New top-level page: `/backups`

Sidebar entry under the **LEARN** section (between Optimization +
Settings). On the page:

```
┌──────────────────────────────────────────────────────────┐
│  Backups                                                  │
│                                                           │
│  Snapshot the agent's state so you can restore after a    │
│  node loss, before a risky upgrade, or just because.      │
│                                                           │
│  [+ Create snapshot]    Auto-schedule: weekly  [Edit ▸]   │
└──────────────────────────────────────────────────────────┘

  ┌────────────────────────────────────────────────────────┐
  │ ★  2026-06-04 14:23   pre-helm-upgrade-1.0.0-rc.2      │
  │    192 MB  ·  audit:1247 inc:89 trace:12  ·  ✓ ok      │
  │    [Restore]  [Download .tar.gz]  [Delete]              │
  ├────────────────────────────────────────────────────────┤
  │    2026-06-03 02:00   scheduled                         │
  │    188 MB  ·  audit:1198 inc:84 trace:12  ·  ✓ ok      │
  │    [Restore]  [Download .tar.gz]  [Delete]              │
  ├────────────────────────────────────────────────────────┤
  │    2026-06-02 02:00   scheduled                         │
  │    186 MB  ·  audit:1180 inc:80 trace:12  ·  ✗ failed   │
  │    Reason: storage backend unreachable (s3://...).      │
  │    [Retry]  [Delete]                                    │
  └────────────────────────────────────────────────────────┘
```

The **★** marks the snapshot the operator would restore first — most
recent successful snapshot. Failures are visible with a clear cause +
retry button.

#### Create-snapshot dialog

```
┌──────────────────────────────────────────────────────────┐
│  Create snapshot                                          │
│                                                           │
│  Tag (optional):  [pre-helm-upgrade-1.0.0-rc.2_______]    │
│                                                           │
│  Storage backend:                                         │
│    ⦿ Local PVC (default)            ~ /data/backups/      │
│    ◯ S3 bucket                     s3://my-bucket/keos/   │
│    ◯ Azure Blob                    abfss://...            │
│    ◯ GCS bucket                    gs://...               │
│                                                           │
│  This will pause the agent's writes for ~2 seconds while  │
│  it copies the audit log + SQLite stores. The graph      │
│  consumer is unaffected.                                  │
│                                                           │
│                                  [Cancel]  [Create]       │
└──────────────────────────────────────────────────────────┘
```

#### Restore-snapshot dialog (destructive — operator types tag to confirm)

```
┌──────────────────────────────────────────────────────────┐
│  ⚠ Restore snapshot                                      │
│                                                           │
│  Restoring from "pre-helm-upgrade-1.0.0-rc.2"             │
│  (2026-06-04 14:23, 192 MB)                               │
│                                                           │
│  This will:                                               │
│    1. Set agent mode to FROZEN                            │
│    2. Stop the graph + observer + retention pruner        │
│    3. Replace audit.log + calibration.db + baselines.db   │
│       + incidents/ + traces/ with the snapshot.           │
│    4. Re-verify the HMAC chain.                           │
│    5. Restart the agent (the SPA will reconnect).         │
│                                                           │
│  Estimated time: ~30 seconds.                             │
│                                                           │
│  Type the snapshot's tag to confirm:                      │
│  [_______________________________________________]        │
│                                                           │
│                                  [Cancel]  [Restore]      │
└──────────────────────────────────────────────────────────┘
```

#### Auto-schedule editor

```
┌──────────────────────────────────────────────────────────┐
│  Auto-snapshot schedule                                   │
│                                                           │
│  Cadence:                                                 │
│    ⦿ Every  [24]  hours                                   │
│    ◯ Weekly on  [Sunday ▾]  at  [02:00] UTC               │
│    ◯ Off                                                  │
│                                                           │
│  Also snapshot before:                                    │
│    ☑ Helm upgrade (detected by the pre-install hook)      │
│    ☐ Mode flip to active                                  │
│    ☐ Incident burst (≥ 5 escalations in 5 min)            │
│                                                           │
│  Retention:                                               │
│    Keep the last  [7]  snapshots.                         │
│    Older snapshots are deleted automatically.             │
│                                                           │
│                                  [Cancel]  [Save]         │
└──────────────────────────────────────────────────────────┘
```

#### API surface

```
GET  /api/backups
  → list, sorted newest-first
GET  /api/backups/{id}
  → detail
POST /api/backups { tag?: string, storage?: "local|s3|azure|gcs" }
  → { task_id, snapshot_id }      (async)
GET  /api/backups/tasks/{task_id}
  → { status: queued|running|done|failed, progress: 0..100 }
POST /api/backups/{id}/restore { confirm_tag }
  → { task_id }                   (async; auto-flips mode to frozen)
GET  /api/backups/{id}/download
  → application/gzip stream
DELETE /api/backups/{id}
  → 200
GET  /api/backups/schedule
PUT  /api/backups/schedule
  → { cadence, also_before, retention }
```

#### Backend module shape

```
agent/backups/
  __init__.py
  snapshot.py         # wraps the proven scripts/backup_restore_drill code
  storage.py          # Local + S3 + Azure + GCS behind one interface
  scheduler.py        # APScheduler-driven cron + pre-action hooks
  registry.py         # ~/.shp-backups.json index (id, tag, ts, size, status)
  tasks.py            # async task queue + status polling
```

`snapshot.py` reuses the existing seed/backup/restore/verify functions
from `scripts/backup_restore_drill.py` — that code already proves
round-trip integrity. We're only adding storage backends + a task
queue + the operator UI on top.

---

## §4 Phase sequencing

| Phase | Surface | Effort | Ship in |
|---|---|---|---|
| **A** | `/api/cluster/context` endpoint + dashboard "Cluster context" card | **shipped in this commit** | — |
| **B** | Helm `agent.attachment.{mode,nodeName}` + bootstrap node-label hook | 1-2 days | a follow-up |
| **C** | Settings → Data Lifecycle tab (retention sliders + disk usage) + `/api/retention` GET/PUT + overlay store | 2 days | next sprint |
| **D** | First-time-setup wizard route + `/api/setup/{state,complete}` | 3-4 days | next sprint |
| **E** | `/backups` page (scaffold) + `agent/backups/` module + Local PVC backend | 3-4 days | sprint after |
| **F** | S3 / Azure / GCS storage backends + scheduled snapshots | 2-3 days | sprint after |
| **G** | Restore flow + HMAC verification + frozen-during-restore mode guard | 2 days | sprint after |

Total scope: ~2 weeks across two sprints. Each phase is independently
shippable + provides operator value on its own.

---

## §5 Design principles (the architecture meta-points)

These are the rules we'd apply to every future operational feature:

1. **Detect first, configure second.** Probe the cluster before asking
   the operator to fill in values. Komodor + Lens win onboarding by
   doing this; vanilla `helm install` loses by not.
2. **Node-aware, not node-locked.** The agent has a *preferred* home
   node (for self-watchdog, for GPU proximity), but the cluster IS the
   unit of deployment — one agent per cluster, not per node.
3. **One pane of glass.** Retention + backups + cluster context belong
   under Settings, not split across kubectl / Helm / Velero. We're
   here to reduce the operator's tool count, not add to it.
4. **No vendor cloud.** Backups go to a local PVC by default; cloud
   storage is opt-in. Edge product = data sovereignty.
5. **HMAC on every restore.** Velero doesn't verify backups by default;
   we do, because our audit chain is the forensic property. Restore is
   the moment when "is the backup intact?" matters most.
6. **Destructive actions require confirmation by typing the tag.** Vault
   does this. CNPG does this. We do this. Drift between intent and
   action at restore time is unrecoverable.

---

## §6 Open questions worth discussing

1. **Is "one agent per cluster" the right deployment model**, or
   should we eventually support a DaemonSet variant (one agent per
   node, federating to a leader) for very-large edge fleets? The
   DaemonSet model is what Datadog uses; it scales but the
   per-decision-point complexity goes up.

2. **Should the agent take a Kubernetes Snapshot CR** (CSI volume
   snapshot) of its own PVC, vs. tar-of-files? CSI snapshots are
   faster + atomic but require a CSI driver that supports snapshots
   (k3s' local-path-storage does NOT). Tar-of-files works everywhere
   but is slower for big stores.

3. **Where does the snapshot live for restore-after-cluster-loss**?
   Local PVC is useless if the cluster is gone. S3 + offsite is the
   answer. We need to default to *some* offsite for the production
   install story.

4. **Is "before-Helm-upgrade" a snapshot trigger we can detect from
   inside the cluster?** The agent doesn't know when Helm runs; the
   chart could fire a `pre-upgrade` hook that POSTs `/api/backups`
   before the new pod template lands. Worth a short proof.

If you have priorities or constraints I'm missing, this is the right
moment to call them out — most of the design is shaped to be
swappable per-phase.
