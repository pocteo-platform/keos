"""GitOps drift detection.

Phase 15 — broader event surface. Before this phase, the agent only
saw kubelet events + use-case-driven synthetic events. Drift between
live cluster state and a GitOps source of truth was invisible.

Three responsibilities:

1. **Sources of truth.** Pluggable provider abstraction
   (`sources.SourceOfTruth`). v1 ships `ManifestsSource` (read YAML
   files from a directory tree) — the laptop default and the
   simplest production deployment. Future providers
   (ArgoCD `Application` CRs, Flux `Kustomization` CRs, raw Git
   clone) implement the same protocol without touching the
   detector.

2. **Comparator.** Pure-Python YAML diff with a comprehensive
   field whitelist. Fields owned by other controllers (HPA →
   `spec.replicas`, kube-controller-manager → owner refs,
   server-side timestamps, status) are stripped before the diff
   so the operator only sees deltas a human actually caused.
   Per-resource overrides via the
   `pocteo.io/drift-ignore-fields` annotation.

3. **Background detector.** Polls every `DRIFT_TICK_INTERVAL_S`
   (default 120 s). For each manifest in the SoT, fetches the
   live resource via the K8s API, diffs, and publishes
   `infra.events.drift.<kind>` to NATS when non-whitelisted
   fields differ. The agent's existing graph consumer picks
   these up like any other event.

The `revert_drift` action in `agent.tools` is the inverse — it
re-applies the SoT YAML to the cluster, bringing the live state
back in line.

Failure modes are soft. A missing SoT manifest, an unreachable
K8s API, a malformed YAML file — each is logged at WARNING and
the detector keeps going. Same fail-OPEN-but-empty pattern as
`chaos_observer` and the Phase 13 baseline learner.
"""

from agent.drift.comparator import (
    DEFAULT_WHITELIST_PATHS,
    DriftReport,
    compare_resources,
    strip_server_fields,
)
from agent.drift.detector import (
    DRIFT_EVENT_PRIORITY,
    DriftDetector,
    detector_from_env,
)
from agent.drift.sources import (
    ManifestsSource,
    SourceOfTruth,
    TargetManifest,
)

__all__ = [
    "DEFAULT_WHITELIST_PATHS",
    "DRIFT_EVENT_PRIORITY",
    "DriftDetector",
    "DriftReport",
    "ManifestsSource",
    "SourceOfTruth",
    "TargetManifest",
    "compare_resources",
    "detector_from_env",
    "strip_server_fields",
]
