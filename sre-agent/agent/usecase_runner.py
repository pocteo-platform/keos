"""Render a user-defined UseCase into a Falco-shaped event and publish it.

Three steps:
  1. Resolve the target pod (specific name OR label-selector lookup on the
     live cluster). Falls back to the conceptual name if cluster lookup fails.
  2. Render the Falco event from the UseCaseFalcoTemplate, substituting
     `{pod}`, `{ns}`, `{image}` placeholders.
  3. Optionally seed the use-case-attached runbook into Qdrant tagged with
     the use_case_id so future re-seeds can clear it.
  4. Publish the event to NATS subject `infra.events.usecase.<id>`.

Returns the incident_id chosen so the UI can navigate to /pipeline/<id>.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import uuid
from datetime import UTC, datetime
from typing import Any

import nats

from agent.api_models import UseCase
from agent.config import kubectl_auth_args, kubectl_env, settings

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolve_target(use_case: UseCase) -> tuple[str, str]:
    """Return (namespace, pod_name) — pod resolved from label selector if
    the use case didn't pin a specific pod name."""
    ns = use_case.target.namespace
    if use_case.target.pod_name:
        return ns, use_case.target.pod_name
    sel = use_case.target.pod_label_selector or {}
    if sel:
        selector = ",".join(f"{k}={v}" for k, v in sel.items())
        try:
            p = subprocess.run(
                [
                    "kubectl",
                    *kubectl_auth_args(),
                    "get",
                    "pods",
                    "-n",
                    ns,
                    "-l",
                    selector,
                    "-o",
                    "jsonpath={.items[0].metadata.name}",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                env=kubectl_env(),
            )
            name = (p.stdout or "").strip()
            if name:
                return ns, name
        except Exception as exc:  # noqa: BLE001
            log.warning("usecase target lookup failed: %s", exc)
    # Last resort: synthesise a name that won't match any pod (graph will
    # surface this cleanly via execute failure).
    return ns, f"{(list(sel.values())[0] if sel else 'unknown')}"


def _render_event(use_case: UseCase, namespace: str, pod: str) -> dict[str, Any]:
    fields = dict(use_case.falco.output_fields or {})
    # Auto-fill the standard k8s.* fields if the template didn't.
    fields.setdefault("k8s.ns.name", namespace)
    fields.setdefault("k8s.pod.name", pod)
    fields.setdefault("evt.time", _now_iso())

    placeholders = {
        "pod": pod,
        "ns": namespace,
        "image": fields.get("container.image.repository", "?"),
    }
    rendered_output = use_case.falco.output_template
    for k, v in placeholders.items():
        rendered_output = rendered_output.replace("{" + k + "}", str(v))

    # Phase 21 — a security use case must carry source∈{falco,tetragon} +
    # domain=security so node_triage routes it down the security branches.
    is_security = use_case.domain == "security"
    event: dict[str, Any] = {
        "uuid": str(uuid.uuid4()),
        "time": _now_iso(),
        "rule": use_case.falco.rule,
        "priority": use_case.falco.priority,
        "source": "falco" if is_security else "syscall",
        "output": rendered_output,
        "output_fields": fields,
        "use_case_id": use_case.id,
    }
    if is_security:
        event["domain"] = "security"
    return event


def _seed_runbook_if_present(use_case: UseCase) -> None:
    """If the use case ships a runbook, embed it into Qdrant tagged with
    the use_case_id so re-seeding doesn't accumulate duplicates."""
    body = use_case.runbook_en or use_case.runbook_fr
    if not body:
        return
    try:
        # Lazy import: pulls the Qdrant client + Ollama embedding deps.
        from agent.seed_rag import seed_one

        seed_one(slug=f"usecase-{use_case.id}", text=body, tags={"use_case_id": use_case.id})
    except Exception as exc:  # noqa: BLE001
        log.warning("runbook seed failed for use case %s: %s", use_case.id, exc)


async def _publish_async(subject: str, payload: dict[str, Any]) -> None:
    # Phase 21 — security use cases publish to the SECURITY stream;
    # everything else to INFRA. The subject prefix picks the stream.
    is_security = subject.startswith("security.events.")
    stream_name = "SECURITY" if is_security else "INFRA"
    stream_subjects = ["security.events.>"] if is_security else ["infra.events.>"]
    nc = await nats.connect(settings.nats_url)
    try:
        js = nc.jetstream()
        try:
            await js.add_stream(name=stream_name, subjects=stream_subjects)
        except Exception:  # noqa: BLE001 — already-exists is fine
            pass
        await js.publish(subject, json.dumps(payload).encode("utf-8"))
    finally:
        await nc.drain()


def run(use_case: UseCase) -> tuple[str, dict[str, Any]]:
    """Publish the use case's telemetry event AND optionally apply a
    matching Chaos Mesh experiment to the cluster.

    Returns (incident_id, event_dict). The incident_id is generated
    client-side and embedded in the event payload so the agent's
    `node_triage` will use it instead of minting a new one.

    Chaos injection is best-effort — if the wizard's `falco.output_fields`
    carries a `chaos.kind` key, we apply the corresponding ChaosExperiment
    CR with a TTL of 30 s. If kubectl isn't available or the CR fails to
    apply, the use case still publishes its NATS event (the agent will
    just respond to the synthetic shape rather than a real chaos).
    """
    ns, pod = _resolve_target(use_case)
    event = _render_event(use_case, ns, pod)
    _seed_runbook_if_present(use_case)
    incident_id = f"uc-{uuid.uuid4().hex[:10]}"
    event["incident_id"] = incident_id  # passed through to triage

    if use_case.domain == "security":
        # Try the real attack simulation (live eBPF telemetry). Best-effort:
        # if it can't run (no eBPF, no cluster), the synthetic event below
        # still drives the demo.
        _apply_attack_sim_if_requested(use_case, ns, pod, incident_id)
        subject = f"security.events.usecase.{use_case.id}"
    else:
        _apply_chaos_if_requested(use_case, ns, pod, incident_id)
        subject = f"infra.events.usecase.{use_case.id}"

    asyncio.run(_publish_async(subject, event))
    return incident_id, event


# ---------------------------------------------------------------------------
# Attack simulation (Phase 21). When a security use case carries an
# `attack_sim`, apply a short-lived Job that actually performs the suspicious
# action against / next to the target pod so Falco/Tetragon detect it for
# REAL. TTL-cleaned. Best-effort — failure never blocks the synthetic event.
# ---------------------------------------------------------------------------


def _apply_attack_sim_if_requested(use_case: UseCase, ns: str, pod: str, incident_id: str) -> None:
    sim = use_case.attack_sim
    if sim is None:
        return
    # exec_shell runs the suspicious command INSIDE the live target pod (so
    # Falco's "Terminal shell in container" sees the real container context).
    # The other kinds run a throwaway Job in the namespace.
    try:
        if sim.kind == "exec_shell" and pod:
            cmd = sim.command or ["/bin/sh", "-c", "echo keos-attack-sim; sleep 1"]
            p = subprocess.run(
                ["kubectl", *kubectl_auth_args(), "exec", "-n", ns, pod, "--", *cmd],
                capture_output=True,
                text=True,
                timeout=15,
                env=kubectl_env(),
            )
            log.info(
                "attack-sim exec_shell on %s/%s rc=%d for use_case=%s",
                ns,
                pod,
                p.returncode,
                use_case.id,
            )
            return
        # run_miner / sensitive_mount / egress → a TTL'd Job.
        cr = _build_attack_job(sim, ns, use_case, incident_id)
        p = subprocess.run(
            ["kubectl", *kubectl_auth_args(), "apply", "-f", "-"],
            input=json.dumps(cr),
            capture_output=True,
            text=True,
            timeout=15,
            env=kubectl_env(),
        )
        if p.returncode == 0:
            log.info("attack-sim %s applied for use_case=%s", sim.kind, use_case.id)
        else:
            log.warning("attack-sim apply failed (rc=%d): %s", p.returncode, p.stderr.strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("attack-sim skipped (%s) — synthetic event still fires", exc)


def _build_attack_job(sim: Any, ns: str, use_case: UseCase, incident_id: str) -> dict[str, Any]:
    """A Job that performs the suspicious action. ttlSecondsAfterFinished=60
    so it self-cleans. sensitive_mount mounts the host containerd/docker
    socket path; egress dials an external IP; run_miner masquerades as xmrig."""
    name = f"keos-sim-{use_case.id[:18]}-{incident_id[-6:]}"
    cmd = sim.command or ["sh", "-c", "echo keos-attack-sim; sleep 2"]
    container: dict[str, Any] = {
        "name": "sim",
        "image": sim.image,
        "command": cmd,
    }
    pod_spec: dict[str, Any] = {
        "restartPolicy": "Never",
        "containers": [container],
    }
    if sim.kind == "sensitive_mount":
        # Mount the host's /var/run so the docker/containerd socket is
        # visible inside the container — the escape primitive Falco flags.
        container["volumeMounts"] = [{"name": "hostrun", "mountPath": "/var/run", "readOnly": True}]
        pod_spec["volumes"] = [{"name": "hostrun", "hostPath": {"path": "/var/run"}}]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": {"keos.pocteo/attack-sim": use_case.id, "keos.pocteo/incident": incident_id},
        },
        "spec": {
            "ttlSecondsAfterFinished": 60,
            "backoffLimit": 0,
            "template": {"metadata": {"labels": {"app": "keos-attack-sim"}}, "spec": pod_spec},
        },
    }


# ---------------------------------------------------------------------------
# Chaos Mesh integration. Reads `falco.output_fields["chaos.kind"]` from the
# use case. If absent, this is a no-op (synthetic-only use case).
# ---------------------------------------------------------------------------

_CHAOS_KIND_API = {
    "PodChaos": "chaos-mesh.org/v1alpha1",
    "NetworkChaos": "chaos-mesh.org/v1alpha1",
    "StressChaos": "chaos-mesh.org/v1alpha1",
    "IOChaos": "chaos-mesh.org/v1alpha1",
}


def _apply_chaos_if_requested(use_case: UseCase, ns: str, pod: str, incident_id: str) -> None:
    fields = use_case.falco.output_fields or {}
    kind = fields.get("chaos.kind", "")
    if kind not in _CHAOS_KIND_API:
        return

    cr = _build_chaos_cr(kind, use_case, ns, pod, incident_id)
    if cr is None:
        return

    import subprocess

    try:
        p = subprocess.run(
            ["kubectl", *kubectl_auth_args(), "apply", "-f", "-"],
            input=json.dumps(cr),
            capture_output=True,
            text=True,
            timeout=15,
            env=kubectl_env(),
        )
        if p.returncode == 0:
            log.info("chaos: applied %s for use_case=%s (%s)", kind, use_case.id, p.stdout.strip())
        else:
            log.warning("chaos: kubectl apply failed (rc=%d): %s", p.returncode, p.stderr.strip())
    except Exception as exc:  # noqa: BLE001
        log.warning("chaos: apply skipped (%s)", exc)


def _build_chaos_cr(
    kind: str, use_case: UseCase, ns: str, pod: str, incident_id: str
) -> dict[str, Any] | None:
    """Render a ChaosExperiment CR. Generic skeleton; specific kinds get
    minimal opinionated specs. TTLSecondsAfterFinished=30 so chaos auto-
    cleans without operator intervention."""
    name = f"uc-{use_case.id[:24]}-{incident_id[-6:]}"
    fields = use_case.falco.output_fields or {}
    duration = str(fields.get("chaos.duration", "30s"))
    selector = {"namespaces": [ns]}
    if pod:
        selector["fieldSelectors"] = {"metadata.name": pod}

    base = {
        "apiVersion": _CHAOS_KIND_API[kind],
        "kind": kind,
        "metadata": {
            "name": name,
            "namespace": "chaos-mesh",
            "labels": {"pocteo.io/use-case": use_case.id, "pocteo.io/incident": incident_id},
        },
        "spec": {
            "selector": selector,
            "mode": fields.get("chaos.mode", "one"),
            "duration": duration,
        },
    }
    if kind == "PodChaos":
        base["spec"]["action"] = fields.get("chaos.action", "pod-kill")
    elif kind == "StressChaos":
        base["spec"]["stressors"] = {
            "memory": {
                "workers": int(fields.get("chaos.workers", 1) or 1),
                "size": fields.get("chaos.size", "256MB"),
            },
        }
    elif kind == "NetworkChaos":
        base["spec"]["action"] = fields.get("chaos.action", "delay")
        base["spec"]["delay"] = {"latency": fields.get("chaos.latency", "200ms")}
    elif kind == "IOChaos":
        base["spec"]["action"] = fields.get("chaos.action", "latency")
        base["spec"]["delay"] = fields.get("chaos.io_delay", "100ms")
        base["spec"]["volumePath"] = fields.get("chaos.volume", "/tmp")
        base["spec"]["path"] = fields.get("chaos.path", "/tmp/*")
    return base
