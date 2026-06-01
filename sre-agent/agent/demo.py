"""End-to-end demo driver invoked by ``make demo`` / ``.\\make.ps1 demo``.

Narrative (single synchronous process, no log-tailing needed):

  1. Sanity-check every dependency (compose services, models, cluster, CRD).
  2. Reset AgentMode to ``observe-only``.
  3. Run scenario 1 → expect the graph to escalate at policy_gate.
  4. Patch AgentMode to ``active``.
  5. Run scenario 1 again → expect execute + verify healed=true.
  6. Print a two-column summary of both runs plus the last 30 audit lines.

If anything in step 1 is missing, the demo fails loudly with the exact
remedy command to run (``make up``, ``make seed``, ``make cluster``, etc.)
so the operator never gets a confusing partial failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from agent.audit import AuditLogger
from agent.config import settings
from agent.graph import run_once
from agent.llm import warmup_chat_model
from agent.logging_utils import configure_logging
from agent.mode_store import mode_store
from agent.synthetic_events import build_event

log = logging.getLogger("demo")


_PATCHES_DIR = Path(__file__).resolve().parent.parent / "manifests" / "patches"


# ---------------------------------------------------------------------------
# Pretty-printing helpers (no third-party deps — works in any terminal).
# ---------------------------------------------------------------------------

_GREEN = "\x1b[32m"
_RED = "\x1b[31m"
_CYAN = "\x1b[36m"
_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _h1(msg: str) -> None:
    print(f"\n{_BOLD}{_CYAN}{'=' * 78}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {msg}{_RESET}")
    print(f"{_BOLD}{_CYAN}{'=' * 78}{_RESET}")


def _h2(msg: str) -> None:
    print(f"\n{_BOLD}--- {msg} ---{_RESET}")


def _ok(msg: str) -> None:
    print(f"  {_GREEN}OK{_RESET}  {msg}")


def _bad(msg: str) -> None:
    print(f"  {_RED}!!{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}..{_RESET}  {msg}")


# ---------------------------------------------------------------------------
# Preflight: every external dependency must respond before we start the clock.
# ---------------------------------------------------------------------------


def _preflight() -> None:
    _h1("Preflight")
    errors: list[str] = []

    # 1. Ollama + both models
    try:
        r = httpx.get(f"{settings.ollama_host}/api/tags", timeout=5.0)
        r.raise_for_status()
        names = {m["name"] for m in r.json().get("models", [])}
        for needed in (settings.chat_model, settings.embed_model):
            # nomic-embed-text installs as "nomic-embed-text:latest"
            if not any(
                n == needed or n.startswith(needed + ":") or n.split(":")[0] == needed.split(":")[0]
                for n in names
            ):
                errors.append(f"Ollama is missing model {needed!r}. Run: make pull-models")
        if not errors:
            _ok(f"Ollama up at {settings.ollama_host} ({len(names)} models)")
    except Exception as exc:
        errors.append(f"Ollama unreachable at {settings.ollama_host}: {exc}. Run: make up")

    # 2. Qdrant collection
    try:
        r = httpx.get(
            f"{settings.qdrant_url}/collections/{settings.qdrant_collection}", timeout=5.0
        )
        r.raise_for_status()
        n = int(r.json()["result"]["points_count"])
        if n < 1:
            errors.append(
                f"Qdrant collection {settings.qdrant_collection!r} is empty. Run: make seed"
            )
        else:
            _ok(f"Qdrant collection {settings.qdrant_collection!r} has {n} points")
    except Exception as exc:
        errors.append(f"Qdrant unreachable: {exc}. Run: make up && make seed")

    # 3. OPA health + agent.authz package present
    try:
        r = httpx.get(f"{settings.opa_url}/health", timeout=5.0)
        r.raise_for_status()
        r2 = httpx.get(f"{settings.opa_url}/v1/policies", timeout=5.0)
        r2.raise_for_status()
        ids = [p.get("id", "") for p in r2.json().get("result", [])]
        if not any("agent.rego" in i for i in ids):
            errors.append("OPA is up but agent.rego isn't loaded. Run: docker compose restart opa")
        else:
            _ok("OPA up; agent.rego loaded")
    except Exception as exc:
        errors.append(f"OPA unreachable at {settings.opa_url}: {exc}. Run: make up")

    # 4. Cluster reachable + AgentMode CR present
    try:
        p = subprocess.run(
            [
                "kubectl",
                "--kubeconfig",
                settings.kubeconfig,
                "get",
                "agentmode",
                settings.agent_mode_name,
                "-o",
                "jsonpath={.spec.mode}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if p.returncode != 0:
            errors.append(f"Cluster/AgentMode missing: {p.stderr.strip()}. Run: make cluster")
        else:
            _ok(f"k3d cluster reachable; AgentMode default mode = {p.stdout.strip()!r}")
    except Exception as exc:
        errors.append(f"kubectl failed: {exc}. Run: make cluster")

    if errors:
        for e in errors:
            _bad(e)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Mode flip helper.
# ---------------------------------------------------------------------------


def _patch_mode(target: str) -> None:
    patch_file = _PATCHES_DIR / f"mode-{target}.yaml"
    if not patch_file.exists():
        raise FileNotFoundError(f"missing patch file {patch_file}")
    p = subprocess.run(
        [
            "kubectl",
            "--kubeconfig",
            settings.kubeconfig,
            "patch",
            "agentmode",
            settings.agent_mode_name,
            "--type=merge",
            "--patch-file",
            str(patch_file),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if p.returncode != 0:
        raise RuntimeError(f"kubectl patch failed: {p.stderr.strip()}")
    # Give the watch loop a beat to observe the change before the next event.
    time.sleep(2)


def _wait_for_mode(expected: str, timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if mode_store.get() == expected:
            return
        time.sleep(0.5)
    raise RuntimeError(
        f"mode_store still reports {mode_store.get()!r} after {timeout_s}s (expected {expected!r})"
    )


# ---------------------------------------------------------------------------
# Summary printer.
# ---------------------------------------------------------------------------


def _print_summary(label: str, final: dict[str, Any]) -> None:
    _h2(label)
    print(f"  incident_id    : {final.get('incident_id', '')}")
    print(f"  rule           : {final.get('rule', '')}")
    print(f"  severity       : {final.get('severity', '')}")
    print(f"  agent_mode     : {final.get('agent_mode', '')}")
    findings = final.get("k8sgpt_findings", []) or []
    if findings:
        print(f"  k8sgpt_findings: {len(findings)} matched")
        for f in findings[:3]:
            print(f"    - {f.get('kind', '?')} {f.get('name', '?')}: {f.get('error', '')[:90]}")
    diag = final.get("diagnosis", {}) or {}
    print(f"  diagnosis      : {diag.get('summary', '')[:120]}")
    print(f"  root_cause     : {diag.get('root_cause', '')[:120]}")
    print(f"  confidence     : {diag.get('confidence', '')}")
    if diag.get("notes"):
        print(f"  notes          : {diag.get('notes', '')[:120]}")
    pa = final.get("proposed_action", {}) or {}
    args_repr = json.dumps(pa.get("args", {}), separators=(",", ":"))
    print(f"  proposed_action: {pa.get('tool', '')}  args={args_repr}")
    pol = final.get("policy_decision", {}) or {}
    decision_color = _GREEN if pol.get("allow") else _RED
    print(
        f"  policy_decision: {decision_color}{'ALLOW' if pol.get('allow') else 'DENY'}{_RESET}  "
        f"reasons={pol.get('reasons', '')}"
    )
    ar = final.get("action_result", {}) or {}
    if ar:
        kind = "DRY-RUN" if ar.get("dry_run") else ("OK" if ar.get("success") else "FAIL")
        col = _GREEN if (ar.get("dry_run") or ar.get("success")) else _RED
        print(
            f"  action_result  : {col}{kind}{_RESET}  {ar.get('stdout', '') or ar.get('stderr', '')}"
        )
    ver = final.get("verification", {}) or {}
    if ver:
        col = _GREEN if ver.get("healed") else _RED
        print(f"  verification   : {col}healed={ver.get('healed')}{_RESET}  {ver.get('notes', '')}")
    esc = bool(final.get("escalated"))
    print(f"  escalated      : {(_RED if esc else _GREEN)}{esc}{_RESET}")


def _print_audit_tail(n: int) -> None:
    _h2(f"Audit trail (last {n} lines)")
    audit = AuditLogger()
    rows = audit.tail(n)
    if not rows:
        _info("(audit log is empty)")
        return
    for r in rows:
        ts = r.get("ts", "")[11:23]  # HH:MM:SS.mmm
        inc = r.get("incident_id", "")[:14]
        et = r.get("event_type", "")
        print(f"  {_DIM}{ts}{_RESET} {inc} {_BOLD}{et:<22}{_RESET}", end="")
        # Compact payload preview
        payload = r.get("payload", {})
        if et in {"diagnose"}:
            print(f"conf={payload.get('confidence', '?')} :: {payload.get('summary', '')[:60]}")
        elif et == "plan_action":
            print(
                f"{payload.get('tool', '?')}({json.dumps(payload.get('args', {}), separators=(',', ':'))})"
            )
        elif et == "policy_gate":
            print(
                f"allow={payload.get('allow', '?')} mode={payload.get('evaluated_with_mode', '?')} "
                f"reasons={payload.get('reasons', [])}"
            )
        elif et == "execute":
            outcome = (
                "DRY-RUN"
                if payload.get("dry_run")
                else ("ok" if payload.get("success") else "FAIL")
            )
            print(f"{outcome} {payload.get('tool', '')}")
        elif et == "verify":
            print(f"healed={payload.get('healed', '?')} :: {payload.get('notes', '')[:60]}")
        elif et == "escalate":
            print(f"reason={payload.get('reason', '')[:80]}")
        elif et == "complete":
            print(f"escalated={payload.get('escalated', '?')} healed={payload.get('healed', '?')}")
        else:
            print()  # newline for events with no notable payload preview


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="self-healing PoC demo")
    parser.add_argument(
        "--scenario",
        type=int,
        default=1,
        help="which synthetic scenario to use (1=shell-in-container default; "
        "6=K8sGPT enrichment showcase)",
    )
    parser.add_argument(
        "--single",
        action="store_true",
        help="run only the active-mode pass (no observe-only round trip)",
    )
    args = parser.parse_args()

    configure_logging()
    _h1(f"Self-healing K8s Edge Agent -- Demo (scenario {args.scenario})")
    _preflight()

    _h2("Starting mode_store watch + warming chat model")
    mode_store.start()
    warmup_chat_model()

    final_observe: dict[str, Any] | None = None

    if not args.single:
        # Step 1: observe-only.
        _h1("Step 1 -- observe-only: agent should escalate")
        _patch_mode("observe")
        _wait_for_mode("observe-only")
        _ok("AgentMode now = observe-only")

        event = build_event(args.scenario)
        _info(
            f"publishing scenario {args.scenario} ({event['rule']}) targeting pod {event['output_fields']['k8s.pod.name']}"
        )
        t0 = time.monotonic()
        final_observe = run_once(event)
        _info(f"run_once took {time.monotonic() - t0:.1f}s")
        _print_summary("Observe-only run", final_observe)

    # Step 2: active.
    _h1("Step 2 -- active: agent should execute and verify healed")
    _patch_mode("active")
    _wait_for_mode("active")
    _ok("AgentMode now = active")

    event2 = build_event(args.scenario)
    _info(
        f"publishing scenario {args.scenario} ({event2['rule']}) targeting pod {event2['output_fields']['k8s.pod.name']}"
    )
    t1 = time.monotonic()
    final_active = run_once(event2)
    _info(f"run_once took {time.monotonic() - t1:.1f}s")
    _print_summary("Active run", final_active)

    # Step 3: audit tail.
    _print_audit_tail(30)

    # Step 4: terse exit verdict for CI / pipeline use.
    _h1("Demo verdict")
    act_healed = bool((final_active.get("verification") or {}).get("healed"))
    act_escalated = bool(final_active.get("escalated"))
    if final_observe is not None:
        obs_escalated = bool(final_observe.get("escalated"))
        if obs_escalated and act_healed and not act_escalated:
            _ok("observe-only escalated (policy blocked execution)")
            _ok("active healed (pod restarted + verified)")
            return 0
        _bad(
            f"unexpected outcome -- obs_escalated={obs_escalated}  act_healed={act_healed}  "
            f"act_escalated={act_escalated}"
        )
        return 1
    # --single mode: judge only on the active run.
    if act_healed and not act_escalated:
        _ok("active healed (action completed + verified)")
        return 0
    if act_escalated and not act_healed:
        _ok("active escalated (LLM/policy/verify routed to escalate)")
        return 0
    _bad(f"unexpected outcome -- act_healed={act_healed}  act_escalated={act_escalated}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
