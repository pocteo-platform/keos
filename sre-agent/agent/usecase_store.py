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
    # NOTE: the old `lib-crashloop-recovery` starter case was retired
    # in Phase 14.13 because it pointed at `busybox-bad` — an
    # intentionally-broken Deployment we no longer ship. Reproduce
    # the CrashLoopBackOff shape against `nginx-demo` via the wizard's
    # PodChaos / StressChaos template, or ad-hoc via:
    #     kubectl run broken-once --image=does-not-exist:v999 \
    #         --restart=Never -n workloads
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
    # ── Security domain (Phase 21) — 4 workshop scenarios ───────────────────
    # Each maps 1:1 to a security runbook + a synthetic security event
    # (agent/security_events.py). The optional attack_sim fires LIVE eBPF
    # telemetry on real hardware; without it (or without Falco/Tetragon
    # installed) the runner falls back to the synthetic event.
    {
        "id": "lib-reverse-shell",
        "name": "Reverse shell in container",
        "description_en": "An attacker spawns an interactive /bin/sh -i inside a running container — the classic 'we got popped' signal. The agent should isolate the pod to stop lateral movement, capture forensics, and escalate to incident response.",
        "description_fr": "Un attaquant lance un shell interactif /bin/sh -i dans un conteneur — le signal classique de compromission. L'agent doit isoler le pod pour stopper les mouvements latéraux, capturer les preuves et escalader vers la réponse à incident.",
        "tags": ["security", "reverse-shell", "containment"],
        "domain": "security",
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "nginx-demo"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Terminal shell in container",
            "priority": "Critical",
            "output_template": "A shell was spawned in a container with an attached terminal in {ns}/{pod}",
            "output_fields": {
                "event.type": "ProcessExec",
                "proc.cmdline": "/bin/sh -i",
                "proc.pname": "nginx",
            },
        },
        "attack_sim": {"kind": "exec_shell", "command": ["/bin/sh", "-i"], "image": "busybox:1.36"},
        "expectation": {
            "diagnosis_keywords": ["shell", "reverse", "container"],
            "proposed_tool": "isolate_pod",
            "policy_allow": True,
            "outcome": "escalated",
        },
        "runbook_en": "# Reverse shell\n\n**Symptom:** interactive `/bin/sh -i` spawned inside an app container.\n\n**Containment:** `isolate_pod` (deny-all NetworkPolicy) to cut the C2 channel, then `capture_forensics`.\n\n**Escalation:** always `page_security` — a confirmed reverse shell is an incident, not an SRE event.",
        "runbook_fr": "# Shell inversé\n\n**Symptôme :** `/bin/sh -i` interactif lancé dans un conteneur applicatif.\n\n**Confinement :** `isolate_pod` (NetworkPolicy deny-all) pour couper le canal C2, puis `capture_forensics`.\n\n**Escalade :** toujours `page_security` — un shell inversé confirmé est un incident, pas un événement SRE.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": "lib-crypto-miner",
        "name": "Crypto-miner / suspicious process",
        "description_en": "An unexpected miner binary (xmrig) starts inside a pod and dials a mining pool — the most common post-exploitation payload. The agent isolates the pod (severing the pool connection), captures the wallet/pool IOCs, and escalates.",
        "description_fr": "Un binaire de minage inattendu (xmrig) démarre dans un pod et contacte un pool de minage — la charge utile post-exploitation la plus courante. L'agent isole le pod, capture les IOC (portefeuille/pool) et escalade.",
        "tags": ["security", "crypto-miner", "resource-abuse"],
        "domain": "security",
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "nginx-demo"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Launch suspicious tool / crypto-miner detected",
            "priority": "Critical",
            "output_template": "Process matching a miner signature started in {ns}/{pod}: xmrig",
            "output_fields": {
                "event.type": "ProcessExec",
                "proc.name": "xmrig",
                "net.dst.port": "4444",
            },
        },
        "attack_sim": {
            "kind": "run_miner",
            "command": ["sh", "-c", "exec -a xmrig sleep 60"],
            "image": "busybox:1.36",
        },
        "expectation": {
            "diagnosis_keywords": ["miner", "xmrig", "suspicious"],
            "proposed_tool": "isolate_pod",
            "policy_allow": True,
            "outcome": "escalated",
        },
        "runbook_en": "# Crypto-miner\n\n**Symptom:** a known miner binary (xmrig) or a process dialing a mining pool.\n\n**Containment:** `isolate_pod` — severing egress stops the mining and any C2. `capture_forensics` for the wallet/pool IOCs.\n\n**Escalation:** `page_security`; hunt the same miner across the fleet.",
        "runbook_fr": "# Mineur de crypto\n\n**Symptôme :** un binaire de minage connu (xmrig) ou un processus contactant un pool de minage.\n\n**Confinement :** `isolate_pod` — couper l'egress arrête le minage et tout C2. `capture_forensics` pour les IOC.\n\n**Escalade :** `page_security` ; rechercher le même mineur sur toute la flotte.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": "lib-priv-esc",
        "name": "Privilege escalation / sensitive mount",
        "description_en": "A container mounts the container-runtime socket (/var/run/docker.sock) — a direct host-escape primitive. The agent isolates the pod, captures the mount evidence, and escalates with the NODE flagged as potentially compromised.",
        "description_fr": "Un conteneur monte le socket du runtime (/var/run/docker.sock) — une primitive d'évasion directe vers l'hôte. L'agent isole le pod, capture la preuve du montage et escalade en signalant le NŒUD comme potentiellement compromis.",
        "tags": ["security", "privilege-escalation", "container-escape"],
        "domain": "security",
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "nginx-demo"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Sensitive mount launched (container escape primitive)",
            "priority": "Critical",
            "output_template": "Container in {ns}/{pod} mounted /var/run/docker.sock — host-escape primitive",
            "output_fields": {
                "event.type": "ProcessExec",
                "container.mounts": "/var/run/docker.sock:/var/run/docker.sock",
                "container.privileged": "true",
            },
        },
        "attack_sim": {
            "kind": "sensitive_mount",
            "command": ["sh", "-c", "ls -la /var/run/docker.sock || true"],
            "image": "busybox:1.36",
        },
        "expectation": {
            "diagnosis_keywords": ["mount", "escalation", "escape"],
            "proposed_tool": "isolate_pod",
            "policy_allow": True,
            "outcome": "escalated",
        },
        "runbook_en": "# Privilege escalation\n\n**Symptom:** sensitive mount (docker/containerd socket, host /) or capability abuse.\n\n**Containment:** `isolate_pod` + `capture_forensics` (record the mount list + process tree).\n\n**Escalation:** `page_security` and flag the NODE — an escape attempt puts the host in scope (cordon + drain + reimage is a human decision).",
        "runbook_fr": "# Élévation de privilèges\n\n**Symptôme :** montage sensible (socket docker/containerd, / de l'hôte) ou abus de capacité.\n\n**Confinement :** `isolate_pod` + `capture_forensics` (enregistrer les montages + l'arbre des processus).\n\n**Escalade :** `page_security` et signaler le NŒUD — une tentative d'évasion met l'hôte en jeu (cordon + drain + réimage = décision humaine).",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
    {
        "id": "lib-data-exfil",
        "name": "Data exfiltration / suspicious egress",
        "description_en": "A pod opens an outbound connection to an unexpected external IP and pushes data — the exfiltration / C2 stage. The agent applies a deny-egress NetworkPolicy (cutting the leak while keeping the pod observable) and escalates with the destination IOCs.",
        "description_fr": "Un pod ouvre une connexion sortante vers une IP externe inattendue et pousse des données — la phase d'exfiltration / C2. L'agent applique une NetworkPolicy deny-egress (coupant la fuite tout en gardant le pod observable) et escalade avec les IOC de destination.",
        "tags": ["security", "data-exfil", "egress", "c2"],
        "domain": "security",
        "target": {
            "namespace": "workloads",
            "pod_name": None,
            "pod_label_selector": {"app": "nginx-demo"},
            "owner_kind": "Deployment",
        },
        "falco": {
            "rule": "Unexpected outbound connection (possible data exfiltration)",
            "priority": "Error",
            "output_template": "Pod {ns}/{pod} opened an outbound connection to an external IP (not in the egress allowlist)",
            "output_fields": {
                "event.type": "ProcessKprobe",
                "net.dst.ip": "203.0.113.66",
                "net.dst.port": "8443",
            },
        },
        "attack_sim": {
            "kind": "egress",
            "command": ["sh", "-c", "wget -T 3 -qO- http://203.0.113.66:8443/ || true"],
            "image": "busybox:1.36",
        },
        "expectation": {
            "diagnosis_keywords": ["exfil", "egress", "outbound"],
            "proposed_tool": "apply_network_policy",
            "policy_allow": True,
            "outcome": "escalated",
        },
        "runbook_en": "# Data exfiltration\n\n**Symptom:** outbound connection to an unexpected external IP / high egress volume / metadata-endpoint scrape.\n\n**Containment:** `apply_network_policy` (deny-egress, DNS excepted) cuts the leak but keeps the pod observable. `capture_forensics` for the destination IOCs.\n\n**Escalation:** `page_security`; assess what data could have left.",
        "runbook_fr": "# Exfiltration de données\n\n**Symptôme :** connexion sortante vers une IP externe inattendue / fort volume d'egress / scrute du endpoint metadata.\n\n**Confinement :** `apply_network_policy` (deny-egress, DNS excepté) coupe la fuite tout en gardant le pod observable. `capture_forensics` pour les IOC.\n\n**Escalade :** `page_security` ; évaluer quelles données ont pu sortir.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    },
]


def seed_library_if_empty() -> None:
    """Idempotent: writes the library use cases (3 SRE + 4 security) if the
    file doesn't exist. On an existing file, prune_invalid_and_migrate_library
    adds any missing starter entries on startup."""
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
