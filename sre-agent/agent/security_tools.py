"""Security-domain action tools (Phase 21).

The containment + response catalog the agent's `execute` node dispatches to
when `state["domain"] == "security"`. Mirrors `tools.py` exactly: every tool
returns an `ActionResult`, subprocess errors are captured (never raised), and
every tool honours `dry_run`.

Posture: escalate-first. These actions are gated to active mode + high/critical
severity + non-protected namespaces by the OPA matrix (`policies/agent.rego`).
`isolate_pod` and `apply_network_policy` are **reversible** (delete the
NetworkPolicy + remove the quarantine label); `kill_pod` is not.

Design choice — `isolate_pod` writes a NetworkPolicy rather than calling a CNI
plugin directly so it works on any conformant cluster (the SRE agent's whole
portability promise). On a cluster whose CNI doesn't enforce NetworkPolicy the
isolation is advisory; the quarantine label + the escalation still fire, so a
human always gets the signal.
"""

from __future__ import annotations

import json
import logging
import tempfile
from typing import Any

from agent.schemas import ActionResult

# Reuse the SRE tools' subprocess plumbing verbatim — one mental model for
# kubectl auth + error capture across both domains.
from agent.tools import _kubectl, _kubectl_error  # noqa: PLC2701  (intentional internal reuse)

log = logging.getLogger(__name__)

# The quarantine label every containment tool stamps on a pod so an operator
# (and `keosctl`) can list everything the agent has contained.
QUARANTINE_LABEL = "keos.pocteo/quarantine"


def _apply_manifest(manifest: dict[str, Any], timeout: float = 30.0) -> tuple[int, str, str]:
    """`kubectl apply -f -` a dict manifest via a temp file.

    Mirrors the pattern `tools.revert_drift` uses for SoT re-apply — a temp
    file keeps the YAML/JSON off the command line (no quoting hazards) and
    works identically across shells/distros.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        json.dump(manifest, fh)
        path = fh.name
    try:
        return _kubectl(["apply", "-f", path], timeout=timeout)
    finally:
        import os

        try:
            os.unlink(path)
        except OSError:
            pass


def _isolation_error(lcode: int, lerr: str, acode: int, aerr: str) -> str:
    """Build a human-useful error for a failed label+apply containment.

    The old message ("label exit 1 / apply exit 1") told the operator
    nothing — the most common cause is a missing target (namespace/pod not
    found) and the kubectl stderr says exactly that. Fold the real stderr in
    so the SPA's failure banner is actionable. Kept compact (first stderr
    line per step) so it reads cleanly in a chip."""
    parts: list[str] = []
    if lcode != 0:
        detail = (lerr or "").strip().splitlines()
        parts.append(f"label failed (exit {lcode}): {detail[0] if detail else 'no detail'}")
    if acode != 0:
        detail = (aerr or "").strip().splitlines()
        parts.append(f"apply failed (exit {acode}): {detail[0] if detail else 'no detail'}")
    return "; ".join(parts) or "containment failed"


# ---------------------------------------------------------------------------
# isolate_pod — deny-all NetworkPolicy + quarantine label.
# ---------------------------------------------------------------------------


def isolate_pod(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    """Cut a compromised pod off from the network and mark it quarantined.

    Two reversible side effects:
      1. Label the pod `keos.pocteo/quarantine=true`.
      2. Apply a deny-all-ingress+egress NetworkPolicy selecting that label,
         so the pod can neither receive nor initiate traffic while forensics
         run. DNS is intentionally NOT excepted — full isolation.

    Reverse with: `kubectl delete networkpolicy keos-isolate-<pod> -n <ns>` +
    `kubectl label pod <pod> keos.pocteo/quarantine-`.
    """
    np_name = f"keos-isolate-{pod_name}"[:63]
    args = {"pod_name": pod_name, "namespace": namespace, "network_policy": np_name}
    if dry_run:
        return ActionResult(
            tool="isolate_pod",
            args=args,
            dry_run=True,
            success=True,
            stdout=(
                f"(dry-run) would: label pod/{pod_name} {QUARANTINE_LABEL}=true -n {namespace}"
                f" + apply deny-all NetworkPolicy {np_name}"
            ),
        )

    # 1. Quarantine label (so the NetworkPolicy podSelector has something to match).
    lcode, _, lerr = _kubectl(
        ["label", "pod", pod_name, f"{QUARANTINE_LABEL}=true", "-n", namespace, "--overwrite"]
    )

    # 2. Deny-all NetworkPolicy targeting the label.
    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": np_name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "keos-security-agent"},
        },
        "spec": {
            "podSelector": {"matchLabels": {QUARANTINE_LABEL: "true"}},
            "policyTypes": ["Ingress", "Egress"],
            # Empty ingress + egress = deny everything.
            "ingress": [],
            "egress": [],
        },
    }
    acode, aout, aerr = _apply_manifest(manifest)

    ok = lcode == 0 and acode == 0
    return ActionResult(
        tool="isolate_pod",
        args=args,
        dry_run=False,
        success=ok,
        stdout=aout.strip(),
        stderr=(lerr + aerr).strip(),
        error=None if ok else _isolation_error(lcode, lerr, acode, aerr),
        meta={"reversible": True, "network_policy": np_name},
    )


# ---------------------------------------------------------------------------
# apply_network_policy — deny-egress (data-exfil containment).
# ---------------------------------------------------------------------------


def apply_network_policy(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    """Block a pod's outbound traffic while leaving ingress intact.

    Narrower than `isolate_pod`: used for suspected data-exfil where we want
    to stop the leak but keep the pod observable (ingress probes, log scraping)
    while IR investigates. Egress to kube-dns is excepted so the pod doesn't
    crashloop on DNS — only the exfil channel is cut.

    Reversible: delete the NetworkPolicy.
    """
    np_name = f"keos-noexfil-{pod_name}"[:63]
    label_val = f"noexfil-{pod_name}"[:63]
    args = {"pod_name": pod_name, "namespace": namespace, "network_policy": np_name}
    if dry_run:
        return ActionResult(
            tool="apply_network_policy",
            args=args,
            dry_run=True,
            success=True,
            stdout=(
                f"(dry-run) would: apply deny-egress NetworkPolicy {np_name}"
                f" to pod/{pod_name} -n {namespace} (DNS excepted)"
            ),
        )

    sel_label = "keos.pocteo/contain"
    lcode, _, lerr = _kubectl(
        ["label", "pod", pod_name, f"{sel_label}={label_val}", "-n", namespace, "--overwrite"]
    )
    manifest = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": np_name,
            "namespace": namespace,
            "labels": {"app.kubernetes.io/managed-by": "keos-security-agent"},
        },
        "spec": {
            "podSelector": {"matchLabels": {sel_label: label_val}},
            "policyTypes": ["Egress"],
            "egress": [
                # Allow only DNS so the pod stays alive + observable; all
                # other egress (the exfil channel) is denied.
                {
                    "to": [{"namespaceSelector": {}}],
                    "ports": [
                        {"protocol": "UDP", "port": 53},
                        {"protocol": "TCP", "port": 53},
                    ],
                }
            ],
        },
    }
    acode, aout, aerr = _apply_manifest(manifest)
    ok = lcode == 0 and acode == 0
    return ActionResult(
        tool="apply_network_policy",
        args=args,
        dry_run=False,
        success=ok,
        stdout=aout.strip(),
        stderr=(lerr + aerr).strip(),
        error=None if ok else _isolation_error(lcode, lerr, acode, aerr),
        meta={"reversible": True, "network_policy": np_name},
    )


# ---------------------------------------------------------------------------
# kill_pod — force-delete a compromised pod (not reversible).
# ---------------------------------------------------------------------------


def kill_pod(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    """Force-delete a pod. Used when containment-in-place isn't enough — the
    pod is actively malicious and must go now. The owning controller (if any)
    will recreate a fresh pod with a new identity; the malicious process is
    killed. Not reversible (the process state is gone)."""
    args = {"pod_name": pod_name, "namespace": namespace}
    if dry_run:
        return ActionResult(
            tool="kill_pod",
            args=args,
            dry_run=True,
            success=True,
            stdout=(
                f"(dry-run) would: kubectl delete pod {pod_name} -n {namespace}"
                " --grace-period=0 --force"
            ),
        )
    code, out, err = _kubectl(
        ["delete", "pod", pod_name, "-n", namespace, "--grace-period=0", "--force"]
    )
    return ActionResult(
        tool="kill_pod",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=out.strip(),
        stderr=err.strip(),
        error=_kubectl_error(code, err),
        meta={"reversible": False},
    )


# ---------------------------------------------------------------------------
# quarantine_pod — isolate + annotate for a forensics hold.
# ---------------------------------------------------------------------------


def quarantine_pod(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    """The 'preserve evidence' containment: isolate the pod (deny-all
    NetworkPolicy + label) AND annotate it so the owning controller's
    reconcile won't immediately reschedule a replacement that reuses the
    same node/volume. Combines isolate_pod with an evidence-hold annotation.

    Reversible: remove the label + annotation + delete the NetworkPolicy.
    """
    args = {"pod_name": pod_name, "namespace": namespace}
    if dry_run:
        return ActionResult(
            tool="quarantine_pod",
            args=args,
            dry_run=True,
            success=True,
            stdout=(
                f"(dry-run) would: isolate pod/{pod_name} + annotate"
                f" keos.pocteo/forensics-hold -n {namespace}"
            ),
        )
    # Reuse isolate_pod for the network cut + label.
    iso = isolate_pod(pod_name, namespace, dry_run=False)
    # Add the forensics-hold annotation on top.
    acode, _, aerr = _kubectl(
        [
            "annotate",
            "pod",
            pod_name,
            "keos.pocteo/forensics-hold=true",
            "-n",
            namespace,
            "--overwrite",
        ]
    )
    ok = iso.success and acode == 0
    return ActionResult(
        tool="quarantine_pod",
        args=args,
        dry_run=False,
        success=ok,
        stdout=(iso.stdout + "\nannotated forensics-hold").strip(),
        stderr=(iso.stderr + aerr).strip(),
        error=None if ok else f"isolate ok={iso.success} / annotate exit {acode}",
        meta={"reversible": True, "network_policy": (iso.meta or {}).get("network_policy")},
    )


# ---------------------------------------------------------------------------
# capture_forensics — annotate + dump recent logs into the audit trail.
# ---------------------------------------------------------------------------


def capture_forensics(pod_name: str, namespace: str, dry_run: bool = False) -> ActionResult:
    """Read-only evidence capture: annotate the pod with a capture timestamp
    and pull the last 200 log lines into the ActionResult (which the audit
    chain records). Does NOT mutate workload behaviour — safe to run even in
    observe-only-ish flows. Pairs with isolate/quarantine for the full IR
    story: contain, then capture."""
    args = {"pod_name": pod_name, "namespace": namespace}
    if dry_run:
        return ActionResult(
            tool="capture_forensics",
            args=args,
            dry_run=True,
            success=True,
            stdout=f"(dry-run) would: capture last 200 log lines of pod/{pod_name} -n {namespace}",
        )
    code, out, err = _kubectl(
        ["logs", pod_name, "-n", namespace, "--tail=200", "--all-containers=true"]
    )
    # Truncate to keep the audit record bounded.
    captured = out.strip()
    if len(captured) > 8000:
        captured = captured[:8000] + "\n... <truncated to 8 KiB>"
    return ActionResult(
        tool="capture_forensics",
        args=args,
        dry_run=False,
        success=(code == 0),
        stdout=f"captured {len(captured)} bytes of logs",
        stderr=err.strip(),
        error=_kubectl_error(code, err),
        meta={"log_capture": captured} if code == 0 else None,
    )


# ---------------------------------------------------------------------------
# page_security — page the security / IR channel (always allowed).
# ---------------------------------------------------------------------------


def page_security(
    reason: str,
    dry_run: bool = False,
    incident_id: str | None = None,
    namespace: str | None = None,
    pod: str | None = None,
    proposed_tool: str | None = None,
    severity: str | None = None,
) -> ActionResult:
    """Page the security / incident-response channel. The security-domain
    equivalent of `page_human` — same best-effort fan-out (log + Slack +
    generic webhook), but tags the page as a security escalation and prefers
    `SECURITY_SLACK_WEBHOOK_URL` / `SECURITY_PAGE_WEBHOOK_URL` if set,
    falling back to the shared SRE webhooks.

    Always succeeds (the structured log line is the canonical receipt) and is
    always allowed by the OPA matrix — escalation must never be gated."""
    import os

    args = {
        "reason": reason,
        "incident_id": incident_id,
        "namespace": namespace,
        "pod": pod,
        "proposed_tool": proposed_tool,
        "severity": severity,
        "channel": "security",
    }
    log.warning(
        "PAGE_SECURITY: %s (incident=%s ns=%s pod=%s tool=%s sev=%s)",
        reason,
        incident_id,
        namespace,
        pod,
        proposed_tool,
        severity,
    )

    delivery: list[dict[str, Any]] = []
    if not dry_run:
        # Prefer security-specific webhooks; fall back to the shared ones via
        # a temporary env shim so we reuse tools._send_notifications verbatim.
        from agent import tools as _sre_tools

        sec_slack = os.environ.get("SECURITY_SLACK_WEBHOOK_URL", "").strip()
        sec_page = os.environ.get("SECURITY_PAGE_WEBHOOK_URL", "").strip()
        saved = {
            "SLACK_WEBHOOK_URL": os.environ.get("SLACK_WEBHOOK_URL"),
            "PAGE_HUMAN_WEBHOOK_URL": os.environ.get("PAGE_HUMAN_WEBHOOK_URL"),
        }
        try:
            if sec_slack:
                os.environ["SLACK_WEBHOOK_URL"] = sec_slack
            if sec_page:
                os.environ["PAGE_HUMAN_WEBHOOK_URL"] = sec_page
            delivery = _sre_tools._send_notifications(args)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    return ActionResult(
        tool="page_security",
        args=args,
        dry_run=dry_run,
        success=True,
        stdout=f"paged security (dry_run={dry_run}): {reason}",
        meta={"delivery": delivery} if delivery else None,
    )


# ---------------------------------------------------------------------------
# Dispatch table — the graph's execute node picks this when domain=security.
# ---------------------------------------------------------------------------


SECURITY_TOOLS = {
    "isolate_pod": isolate_pod,
    "apply_network_policy": apply_network_policy,
    "kill_pod": kill_pod,
    "quarantine_pod": quarantine_pod,
    "capture_forensics": capture_forensics,
    "page_security": page_security,
}
