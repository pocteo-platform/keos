"""User-defined use cases. Persisted as a single JSON file at
`%USERPROFILE%/.shp-usecases.json` (or `~/.shp-usecases.json` on POSIX).

Why one file: trivially diff-able by humans, atomic write via tmp+rename,
no migrations. The PoC may have at most a few dozen entries.

A use case is a Pydantic-validated `UseCase` (see `agent/api_models.py`).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent.api_models import UseCase

log = logging.getLogger(__name__)

_USECASE_PATH_ENV = "SHP_USECASES_PATH"


def _path() -> Path:
    override = os.environ.get(_USECASE_PATH_ENV)
    if override:
        return Path(override)
    home = Path.home()
    return home / ".shp-usecases.json"


_lock = threading.Lock()


def _now() -> datetime:
    return datetime.now(UTC)


def _load_raw() -> list[dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        log.warning("usecase file %s was malformed; treating as empty", p)
        return []


def _write_raw(items: list[dict[str, Any]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, p)


def list_all() -> list[UseCase]:
    """Validate each persisted entry independently. A single bad entry (e.g.
    one carrying a tool name that was renamed in a later release) is skipped
    with a loud log line instead of 500-ing the whole endpoint."""
    out: list[UseCase] = []
    with _lock:
        for it in _load_raw():
            try:
                out.append(UseCase.model_validate(it))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "usecase_store: skipping invalid entry id=%s — %s",
                    it.get("id", "?"),
                    exc,
                )
    return out


def get(use_case_id: str) -> UseCase | None:
    for it in list_all():
        if it.id == use_case_id:
            return it
    return None


def create(payload: dict[str, Any]) -> UseCase:
    now = _now()
    payload = {
        **payload,
        "id": payload.get("id") or f"uc-{uuid.uuid4().hex[:10]}",
        "created_at": payload.get("created_at") or now,
        "updated_at": now,
    }
    uc = UseCase.model_validate(payload)
    with _lock:
        items = _load_raw()
        items.append(uc.model_dump(mode="json"))
        _write_raw(items)
    return uc


def update(use_case_id: str, patch: dict[str, Any]) -> UseCase | None:
    with _lock:
        items = _load_raw()
        for i, it in enumerate(items):
            if it.get("id") == use_case_id:
                merged = {**it, **patch, "id": use_case_id, "updated_at": _now()}
                uc = UseCase.model_validate(merged)
                items[i] = uc.model_dump(mode="json")
                _write_raw(items)
                return uc
    return None


def delete(use_case_id: str) -> bool:
    with _lock:
        items = _load_raw()
        new_items = [it for it in items if it.get("id") != use_case_id]
        if len(new_items) == len(items):
            return False
        _write_raw(new_items)
        return True


# ---------------------------------------------------------------------------
# Library starter set — pre-seeded on first read if the file is empty.
# ---------------------------------------------------------------------------

LIBRARY_STARTER: list[dict[str, Any]] = [
    {
        "id": "lib-memory-leak",
        "name": "Memory leak / OOM trajectory",
        "description_en": "A customer-facing pod's working-set memory is climbing rapidly post-deploy. The agent should observe the rate, predict OOMKill ETA, and proactively rollout_undo to the previous stable revision.",
        "description_fr": "La mémoire d'un pod exposé grimpe rapidement après un déploiement. L'agent doit observer la pente, prédire l'OOMKill et exécuter un rollout_undo vers la révision stable précédente.",
        "tags": ["memory", "rollout-regression"],
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "nginx-demo"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Container memory leak / OOM trajectory",
            "priority": "Warning",
            "output_template": "Container memory RSS rising +18 MiB/min in {ns}/{pod}",
            "output_fields": {"event.kind": "StressChaos", "memory.growth_rate_mibps": "18"},
        },
        "expectation": {
            "diagnosis_keywords": ["memory", "leak", "rollout"],
            "proposed_tool": "rollout_undo",
            "policy_allow": True,
            "outcome": "healed",
        },
        "runbook_en": "# Memory-leak runbook\n\n**Symptom:** Container RSS rising monotonically post-deploy.\n\n**Action:** rollout_undo to the previous ReplicaSet whose memory was stable.",
        "runbook_fr": "# Runbook fuite mémoire\n\n**Symptôme :** RSS qui croît monotone après un déploiement.\n\n**Action :** rollout_undo vers le ReplicaSet précédent dont la mémoire était stable.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": "lib-crashloop-recovery",
        "name": "Pod in CrashLoopBackOff",
        "description_en": "A workload pod is restart-looping. The agent should distinguish a bad deploy (rollout_undo) from a replica-local crash (restart_pod) using the diagnostic signals.",
        "description_fr": "Un pod tourne en CrashLoopBackOff. L'agent doit distinguer un mauvais déploiement (rollout_undo) d'un crash local à la réplique (restart_pod) grâce aux signaux de diagnostic.",
        "tags": ["availability", "crashloop"],
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "busybox-bad"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Pod restart loop detected",
            "priority": "Error",
            "output_template": "Container in {ns}/{pod} restarted 5x in the last 3 minutes",
            "output_fields": {"event.kind": "BackOff", "restart.count": "5", "exit.code": "1"},
        },
        "expectation": {
            "diagnosis_keywords": ["crashloop", "restart"],
            "proposed_tool": "rollout_undo",
            "policy_allow": True,
            "outcome": "healed",
        },
        "runbook_en": "# CrashLoopBackOff runbook\n\n**Symptom:** Pod restart-looping post-deploy.\n\n**Action:** rollout_undo if a recent rollout is implicated; restart_pod otherwise.",
        "runbook_fr": "# Runbook CrashLoopBackOff\n\n**Symptôme :** Pod en boucle de redémarrage après un déploiement.\n\n**Action :** rollout_undo si un rollout récent est en cause ; sinon restart_pod.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": "lib-node-pressure",
        "name": "Node memory pressure",
        "description_en": "A node reports MemoryPressure=True; pods are at risk of eviction. The agent should cordon the node to stop new scheduling, then page a human to decide on full drain.",
        "description_fr": "Un nœud signale MemoryPressure=True ; les pods risquent l'éviction. L'agent doit cordonner le nœud pour stopper l'ordonnancement, puis avertir un humain pour décider du drain complet.",
        "tags": ["node-health", "capacity"],
        "target": {
            "namespace": "",
            "pod_name": "k3d-poc-server-0",
            "pod_label_selector": None,
            "owner_kind": "Standalone",
        },
        "falco": {
            "rule": "Node under memory pressure",
            "priority": "Critical",
            "output_template": "Node {pod} reports MemoryPressure=True; pods at risk of eviction",
            "output_fields": {"event.kind": "NodeNotReady", "kubelet.reason": "MemoryPressure"},
        },
        "expectation": {
            "diagnosis_keywords": ["memory", "pressure", "node"],
            "proposed_tool": "cordon_node",
            "policy_allow": True,
            "outcome": "healed",
        },
        "runbook_en": "# Node pressure runbook\n\n**Symptom:** Node reports MemoryPressure / DiskPressure.\n\n**Action:** cordon_node to stop new scheduling; drain_node at high severity; page_human for capacity decisions.",
        "runbook_fr": "# Runbook pression du nœud\n\n**Symptôme :** Le nœud signale MemoryPressure / DiskPressure.\n\n**Action :** cordon_node pour stopper l'ordonnancement ; drain_node en haute gravité ; page_human pour la décision capacité.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        # Designed to exercise the OPA matrix L4 deny path so a pending
        # approval shows up in /control. The Notice priority maps to
        # severity=low; the runbook strongly suggests drain_node, which at
        # severity=low is L4 in policies/agent.rego — the gate denies,
        # node_escalate registers an approval, Slack (if configured)
        # gets paged.
        "id": "lib-drain-low-severity",
        "name": "Drain proposal at low severity (approval demo)",
        "description_en": "A node reports gentle pressure (Notice priority). The agent's runbook suggests drain_node, but at low severity the OPA matrix denies the action — the agent escalates and registers a pending approval for an operator to override.",
        "description_fr": "Un nœud signale une pression légère (priorité Notice). Le runbook suggère drain_node, mais à faible gravité la matrice OPA refuse — l'agent escalade et enregistre une approbation en attente qu'un opérateur peut surclasser.",
        "tags": ["approval-demo", "policy-gate", "drain"],
        "target": {
            "namespace": "workloads",
            "pod_name": "k3d-poc-server-0",
            "pod_label_selector": None,
            "owner_kind": "Standalone",
        },
        "falco": {
            "rule": "Node under gentle pressure",
            "priority": "Notice",  # -> severity "low" after triage
            "output_template": "Node {pod} reports gentle pressure; recommend drain to relieve load before symptoms worsen",
            "output_fields": {
                "event.kind": "NodeHasMemoryPressure",
                "kubelet.reason": "MemoryPressure",
                "pressure.level": "low",
            },
        },
        "expectation": {
            "diagnosis_keywords": ["node", "pressure", "drain"],
            "proposed_tool": "drain_node",
            "policy_allow": False,
            "outcome": "escalated",
        },
        "runbook_en": "# Preventive drain runbook\n\n**Symptom:** Node reports gentle MemoryPressure or PIDPressure (Notice level).\n\n**Diagnosis:** The node is healthy but trending poorly. Symptoms haven't materialised yet.\n\n**Remediation:** `drain_node` to relocate workloads before pods get OOM-killed. **Note:** the policy matrix gates drain_node at low severity — this WILL be denied, surfacing the action to an operator via the Pending Approvals queue.",
        "runbook_fr": "# Runbook drain préventif\n\n**Symptôme :** Le nœud signale une pression mémoire ou PID légère (niveau Notice).\n\n**Diagnostic :** Le nœud est sain mais la tendance se dégrade. Les symptômes ne se sont pas encore manifestés.\n\n**Remédiation :** `drain_node` pour relocaliser les charges avant que les pods soient tués par OOM. **Note :** la matrice de politique refuse drain_node à faible gravité — cette action SERA refusée et apparaîtra dans la file d'approbations.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
]


def seed_library_if_empty() -> None:
    """Idempotent: writes the 3 library use cases if the file doesn't exist."""
    p = _path()
    if p.exists():
        return
    with _lock:
        if p.exists():
            return
        _write_raw(LIBRARY_STARTER)


def prune_invalid_and_migrate_library() -> tuple[int, int]:
    """Startup migration. Three jobs:
      1. Drop any persisted entry that no longer validates against the
         current UseCase schema (e.g. uses a tool name that was removed).
      2. Drop any `lib-*` entry whose id is NOT in the current
         LIBRARY_STARTER (kills stale starter use cases from prior releases).
      3. Insert any current LIBRARY_STARTER entries that are missing.

    Returns (n_dropped, n_added). Safe to call on every API startup."""
    current_lib_ids = {e["id"] for e in LIBRARY_STARTER}
    with _lock:
        items = _load_raw()
        kept: list[dict[str, Any]] = []
        dropped = 0
        for it in items:
            entry_id = it.get("id", "")
            # Rule 2: orphaned starter library entry.
            if entry_id.startswith("lib-") and entry_id not in current_lib_ids:
                log.warning("usecase_store: pruning orphaned starter id=%s", entry_id)
                dropped += 1
                continue
            # Rule 1: schema-invalid entry.
            try:
                UseCase.model_validate(it)
                kept.append(it)
            except Exception as exc:  # noqa: BLE001
                log.warning("usecase_store: pruning invalid id=%s — %s", entry_id, exc)
                dropped += 1
        # Rule 3: top up missing library entries.
        kept_ids = {it.get("id") for it in kept}
        added = 0
        for lib_entry in LIBRARY_STARTER:
            if lib_entry["id"] not in kept_ids:
                kept.append(lib_entry)
                added += 1
        if dropped or added:
            _write_raw(kept)
            log.info("usecase_store: migration done — dropped=%d added=%d", dropped, added)
        return dropped, added
