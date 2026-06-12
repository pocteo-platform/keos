"""Synthetic Falco event generator.

Five canned scenarios, one per runbook. Each emits a Falco-shaped JSON
document (rule, priority, output, output_fields) targeting workloads in the
PoC cluster. The agent can't tell these from real Falco events.

Used two ways:
  - `python -m agent.synthetic_events --scenario N` publishes to NATS.
  - `from agent.synthetic_events import build_event` returns the dict
    in-process (used by the `--once` graph entrypoint and by tests).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

import nats

from agent.config import kubectl_auth_args, kubectl_env, settings
from agent.logging_utils import configure_logging

log = logging.getLogger("synthetic_events")

# Subject pattern: infra.events.<kind>. The 7 scenarios map 1:1 to the 7
# SRE runbooks. Each scenario simulates the SHAPE of an infrastructure
# event that would come from real telemetry sources (kubelet events,
# Chaos Mesh experiments, HPA controllers, GitOps drift detectors).
# Scenarios 2 (crashloop) and 3 (image-pull-failure) were retired in
# Phase 14.13 along with their target pods (busybox-bad, broken-image).
# Modern way to reproduce those failure shapes:
#   - via the SPA's Use Case wizard, which injects through Chaos Mesh
#     (real K8s events the chaos_observer reacts to), OR
#   - ad-hoc:  kubectl run broken-once --image=does-not-exist:v999 \
#                 --restart=Never -n workloads
# The remaining scenarios (1, 4-7) target nginx-demo / k3d-poc-server-0
# which the bootstrap guarantees exist.
_SUBJECTS = {
    1: "infra.events.memory_leak",
    4: "infra.events.node_pressure",
    5: "infra.events.rollout_regression",
    6: "infra.events.config_drift",
    7: "infra.events.traffic_surge",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _event(
    scenario: int, rule: str, priority: str, output: str, fields: dict[str, Any]
) -> dict[str, Any]:
    return {
        "uuid": str(uuid.uuid4()),
        "time": _now_iso(),
        "rule": rule,
        "priority": priority,
        "source": "syscall",
        "output": output,
        "output_fields": fields,
        "scenario_id": scenario,
    }


def _resolve_pod_name(namespace: str, app_label: str, fallback: str) -> str:
    """Look up a real running pod matching `app=<label>` in the given namespace.
    Falls back to the conceptual name if the cluster lookup fails so the
    scenario still emits something the graph can run through.
    """
    import subprocess

    try:
        p = subprocess.run(
            [
                "kubectl",
                *kubectl_auth_args(),
                "get",
                "pods",
                "-n",
                namespace,
                "-l",
                f"app={app_label}",
                "-o",
                "jsonpath={.items[0].metadata.name}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            env=kubectl_env(),
        )
        name = p.stdout.strip()
        if name:
            return name
    except Exception:  # noqa: BLE001
        pass
    return fallback


def build_event(scenario: int) -> dict[str, Any]:
    """Construct an SRE/infrastructure event for the given scenario (1..7).

    Each scenario simulates the shape of an event the agent's chaos_observer
    would emit when it sees the corresponding kubelet event / Chaos Mesh
    experiment / GitOps drift detector signal. For scenarios targeting real
    workloads, we resolve the conceptual pod name to the actual running pod
    name so the agent's remediation will succeed.
    """
    if scenario == 1:
        return _event(
            1,
            rule="Container memory leak / OOM trajectory",
            priority="Warning",
            output="Container memory RSS rising +18 MiB/min; OOMKill ETA ~6 min",
            fields={
                "event.kind": "StressChaos",
                "container.name": "nginx",
                "k8s.ns.name": "workloads",
                "k8s.pod.name": _resolve_pod_name("workloads", "nginx-demo", "nginx-demo"),
                "k8s.deployment.name": "nginx-demo",
                "memory.rss_bytes": 658505728,  # ~628 MiB
                "memory.limit_bytes": 805306368,  # ~768 MiB
                "memory.growth_rate_mibps": 18,
                "evt.time": _now_iso(),
            },
        )
    if scenario == 4:
        return _event(
            4,
            rule="Node under memory pressure",
            priority="Critical",
            output="Node k3d-poc-server-0 reports MemoryPressure=True; pods at risk of eviction",
            fields={
                "event.kind": "NodeNotReady",
                "kubelet.reason": "MemoryPressure",
                "k8s.node.name": "k3d-poc-server-0",
                "node.memory_available_bytes": 268435456,  # ~256 MiB
                "node.allocatable_memory_bytes": 8589934592,  # 8 GiB
                "evt.time": _now_iso(),
            },
        )
    if scenario == 5:
        return _event(
            5,
            rule="Rollout regression (new ReplicaSet not progressing)",
            priority="Error",
            output="Deployment nginx-demo rollout stalled: 0/3 new replicas Ready after 5 min",
            fields={
                "event.kind": "DeploymentRolloutStalled",
                "k8s.ns.name": "workloads",
                "k8s.deployment.name": "nginx-demo",
                "k8s.pod.name": _resolve_pod_name("workloads", "nginx-demo", "nginx-demo"),
                "rollout.new_ready_replicas": 0,
                "rollout.desired_replicas": 3,
                "rollout.elapsed_s": 300,
                "evt.time": _now_iso(),
            },
        )
    if scenario == 6:
        return _event(
            6,
            rule="Configuration drift detected",
            priority="Warning",
            output="ConfigMap workloads/app-config differs from Git HEAD: key 'log_level' changed live",
            fields={
                "event.kind": "OutOfSync",
                "gitops.source": "argo-cd",
                "k8s.ns.name": "workloads",
                "k8s.resource.kind": "ConfigMap",
                "k8s.resource.name": "app-config",
                "drift.keys": "log_level",
                "evt.time": _now_iso(),
            },
        )
    if scenario == 7:
        return _event(
            7,
            rule="Traffic surge / hot pod",
            priority="Warning",
            output="3 of 8 nginx-demo replicas at 95%+ CPU; HPA lagging the demand curve",
            fields={
                "event.kind": "HPALagging",
                "k8s.ns.name": "workloads",
                "k8s.deployment.name": "nginx-demo",
                "k8s.pod.name": _resolve_pod_name("workloads", "nginx-demo", "nginx-demo"),
                "hpa.current_replicas": 3,
                "hpa.desired_replicas": 8,
                "cpu.utilisation_pct": 95,
                "evt.time": _now_iso(),
            },
        )
    raise ValueError(
        f"unknown scenario {scenario}; use one of {sorted(_SUBJECTS)} "
        "(scenarios 2 + 3 were retired — reproduce CrashLoopBackOff / "
        "ImagePullBackOff via the Use Case wizard or `kubectl run`)"
    )


async def _publish(scenario: int) -> None:
    event = build_event(scenario)
    subject = _SUBJECTS[scenario]
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()
    try:
        # Stream subjects now live under infra.events.> (was security.events.>).
        # We still allow the old name for backward-compat replay from existing
        # NATS streams; new publishes go to the new subject.
        await js.add_stream(name="INFRA", subjects=["infra.events.>"])
    except Exception as exc:  # noqa: BLE001
        log.debug("add_stream noted: %s", exc)
    ack = await js.publish(subject, json.dumps(event).encode("utf-8"))
    log.info("published scenario=%d subject=%s seq=%s", scenario, subject, ack.seq)
    await nc.drain()


def main() -> int:
    p = argparse.ArgumentParser(description="publish a synthetic Falco event")
    p.add_argument("--scenario", type=int, required=True, choices=list(_SUBJECTS))
    p.add_argument(
        "--print-only",
        action="store_true",
        help="print the event JSON to stdout; don't publish to NATS",
    )
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    configure_logging("DEBUG" if args.verbose else None)

    if args.print_only:
        print(json.dumps(build_event(args.scenario), indent=2))
        return 0
    asyncio.run(_publish(args.scenario))
    return 0


if __name__ == "__main__":
    sys.exit(main())
