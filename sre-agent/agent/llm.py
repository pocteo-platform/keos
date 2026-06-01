"""LLM wrappers for the diagnose + plan_action nodes.

We call Ollama's /api/generate with `format: "json"` and `temperature: 0.1`
to nudge the model toward valid, deterministic JSON. We still validate every
response with pydantic — `format: "json"` only guarantees parseable JSON, not
schema conformance. On ValidationError we retry ONCE with an explicit
"your last response failed validation with X, here is the schema again" hint;
on a second failure the caller (graph.py) routes the incident to `escalate`.

Prompts are module-level constants so they can be inspected and diffed.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from agent.config import settings
from agent.schemas import Diagnosis, ProposedAction

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts — kept as constants so we can diff and unit-test them.
# ---------------------------------------------------------------------------

DIAGNOSE_SYSTEM = """\
You are an autonomous SRE agent for a Kubernetes cluster.

You have up to THREE input sources:
  1. The raw infrastructure telemetry event (always present).
  2. Top runbook excerpts retrieved by similarity (1-3 items).
  3. Structured findings about the live cluster state (0+ items, may be empty).

When cluster signals exist, treat them as ground-truth observations of the
cluster's current state. The telemetry event tells you what was DETECTED;
the cluster signals tell you what is actually WRONG right now. If the two
disagree (e.g., the event says "container failed to start" but the signal
says "ImagePullBackOff: manifest unknown"), prefer the cluster signal for
`root_cause` and note the reconciliation in `notes`.

Produce a SINGLE JSON OBJECT with exactly these five keys:

  "summary"     : string, 1-2 sentences. WHAT happened, in plain English.
  "root_cause"  : string, 1 sentence. The most-likely underlying cause.
  "confidence"  : float in [0.0, 1.0]. Use >= 0.6 when at least one source
                  clearly matches; use < 0.6 only if you cannot choose
                  between multiple incompatible hypotheses.
  "evidence"    : array of 1 to 5 short strings, each citing a specific
                  fact you used. Prefix with the source: "event:",
                  "runbook:", or "signal:" so the operator can trace your
                  reasoning.
  "notes"       : string, MAY be empty. Use to flag conflicts between sources,
                  e.g. "event said generic failure but signal confirms
                  ImagePullBackOff".

Example of the EXACT shape (values illustrative):
{"summary":"Pod is in CrashLoopBackOff after the latest rollout.","root_cause":"A recent Deployment revision introduced a bad command.","confidence":0.85,"evidence":["event:reason=BackOff","runbook:002 covers crashloop"],"notes":""}

Hard rules:
  - Return ONLY that JSON object. No prose. No markdown. No code fences.
  - Do NOT echo the input. Do NOT add extra keys.
  - The five keys above are required and sufficient. `notes` may be "".
"""

PLAN_ACTION_SYSTEM = """\
You are an autonomous SRE agent. Choose ONE remediation tool from this exact
catalog and return a SINGLE JSON OBJECT describing your choice.

Catalog (tool name and required args):
  restart_pod        {"pod_name": str, "namespace": str}
  rollout_undo       {"deployment_name": str, "namespace": str}
  scale_deployment   {"deployment_name": str, "namespace": str, "replicas": int}
  cordon_node        {"node_name": str}
  uncordon_node      {"node_name": str}
  drain_node         {"node_name": str, "grace_period_s": int}
  page_human         {"reason": str}

Output schema (exactly these five keys, in any order):
  "tool"       : string from the catalog above.
  "args"       : object matching that tool's required args.
  "scope"      : one of "pod", "ns", "deploy", "node", "n/a".
  "reversible" : boolean.
  "rationale"  : string, 1-2 sentences.

Example of the EXACT shape (values illustrative):
{"tool":"rollout_undo","args":{"deployment_name":"nginx-demo","namespace":"workloads"},"scope":"deploy","reversible":true,"rationale":"Memory leak began at the last rollout; reverting to the previous ReplicaSet restores stability."}

Hard rules:
  - NEVER target namespaces kube-system or kube-public.
  - When the runbook says "page_human" or you are unsure, choose "page_human".
  - restart_pod, rollout_undo, scale_deployment, cordon_node, uncordon_node,
    drain_node are all reversible.
  - FailedScheduling with reason "nodes are unschedulable" almost always
    means a node is cordoned. The right remediation is uncordon_node on
    that node.
  - Return ONLY the JSON object. No prose. No markdown. No code fences.
"""

# ---------------------------------------------------------------------------
# JSON extraction helper — Ollama's format:"json" usually returns clean JSON
# but a 3B model occasionally wraps in ```json fences or adds prose. Strip
# those defensively before pydantic.
# ---------------------------------------------------------------------------

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    s = _JSON_FENCE.sub("", text).strip()
    # If the model dumped multiple objects, take the first balanced one.
    if s.startswith("{"):
        depth = 0
        for i, ch in enumerate(s):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return s[: i + 1]
    return s


# ---------------------------------------------------------------------------
# Low-level Ollama call.
# ---------------------------------------------------------------------------


# Retry policy for transient Ollama errors (connection drops, 502/503/504).
# Three total attempts with exponential backoff: 1 s, 2 s, 4 s. Total worst-case
# extra wait = 7 s, which is small compared to the ~30-60 s LLM call itself
# and lets a brief Ollama restart finish without losing the graph run.
_LLM_RETRYABLE_STATUS = frozenset({500, 502, 503, 504})
_LLM_RETRY_ATTEMPTS = 3
_LLM_RETRY_BACKOFF_S = (1.0, 2.0, 4.0)


class OllamaUnavailable(RuntimeError):
    """Raised when all `_LLM_RETRY_ATTEMPTS` attempts have failed. Callers
    (graph nodes) treat this as a hard "escalate" trigger — the LLM cannot
    diagnose or plan, and silent failure would be worse than paging a human."""


def _generate(system: str, user: str, timeout: float = 300.0) -> str:
    """Synchronous JSON-mode generate. Returns the raw response string.

    timeout=300s is intentional: the first call loads ~2 GB of Qwen2.5-3B
    weights from disk on CPU, which can take 60-90 s on a laptop SSD. Once
    the model is resident, subsequent calls finish in 5-30 s. Use
    `warmup_chat_model()` once at startup to pay that cost early.

    Retries the HTTP call up to `_LLM_RETRY_ATTEMPTS` times on connection
    errors and 5xx responses. On final failure raises `OllamaUnavailable`,
    which graph nodes catch + route to escalate.
    """
    import time as _time

    body = {
        "model": settings.chat_model,
        "system": system,
        "prompt": user,
        "format": "json",
        "stream": False,
        "keep_alive": "30m",  # keep the model warm between calls in --serve
        "options": {
            "temperature": 0.1,
            "num_predict": 512,
            "top_p": 0.9,
        },
    }
    last_err: Exception | None = None
    for attempt in range(_LLM_RETRY_ATTEMPTS):
        try:
            resp = httpx.post(f"{settings.ollama_host}/api/generate", json=body, timeout=timeout)
            if resp.status_code in _LLM_RETRYABLE_STATUS:
                last_err = httpx.HTTPStatusError(
                    f"ollama returned {resp.status_code}", request=resp.request, response=resp
                )
                log.warning(
                    "ollama %s (attempt %d/%d); retrying in %.1fs",
                    resp.status_code,
                    attempt + 1,
                    _LLM_RETRY_ATTEMPTS,
                    _LLM_RETRY_BACKOFF_S[attempt],
                )
            else:
                resp.raise_for_status()
                return resp.json().get("response", "")
        except httpx.RequestError as exc:
            # Connection error / timeout / DNS — retryable.
            last_err = exc
            log.warning(
                "ollama request failed (%s) (attempt %d/%d); retrying in %.1fs",
                type(exc).__name__,
                attempt + 1,
                _LLM_RETRY_ATTEMPTS,
                _LLM_RETRY_BACKOFF_S[attempt],
            )
        if attempt + 1 < _LLM_RETRY_ATTEMPTS:
            _time.sleep(_LLM_RETRY_BACKOFF_S[attempt])

    # All attempts failed. Raise so graph callers route to escalate.
    raise OllamaUnavailable(
        f"ollama unreachable after {_LLM_RETRY_ATTEMPTS} attempts: {last_err}"
    ) from last_err


def warmup_chat_model(timeout: float = 300.0) -> None:
    """Send a tiny request so the model is loaded into Ollama's memory before
    the real diagnose/plan_action call. Idempotent; safe to call repeatedly."""
    try:
        httpx.post(
            f"{settings.ollama_host}/api/generate",
            json={
                "model": settings.chat_model,
                "prompt": "ok",
                "stream": False,
                "keep_alive": "30m",
                "options": {"num_predict": 1, "temperature": 0.0},
            },
            timeout=timeout,
        ).raise_for_status()
        log.info("chat model %s warmed", settings.chat_model)
    except Exception as exc:  # noqa: BLE001
        log.warning("warmup failed (will rely on first diagnose call): %s", exc)


# ---------------------------------------------------------------------------
# Public API: diagnose() and plan_action() each retry once on validation
# failure, then surface None so the graph can escalate.
# ---------------------------------------------------------------------------


def _call_with_retry(system: str, user: str, model_cls: type) -> Any | None:
    """Generate, parse, validate. On ValidationError or JSONDecodeError, retry
    once with an explicit "your last response was invalid" follow-up message.

    Returns None on:
      - Two consecutive parse/validation failures (model can't produce valid JSON)
      - `OllamaUnavailable` from `_generate` (transport failures exhausted retries)

    Either case maps to the same graph behavior: route to escalate. The
    distinction is preserved in logs + the `keos_escalations_total` reason
    label so operators can tell "LLM down" from "LLM hallucinating".
    """
    try:
        raw = _generate(system, user)
    except OllamaUnavailable as exc:
        log.error("LLM unreachable on first attempt: %s — escalating", exc)
        return None
    parsed = _try_parse(raw, model_cls)
    if parsed is not None:
        return parsed
    log.warning("LLM response failed validation; retrying once. raw=%r", raw[:300])
    retry_user = (
        user + "\n\nYour previous response was invalid JSON or did not match the schema. "
        "Return ONLY the JSON object with the exact keys listed in the system "
        "instructions. Do not include markdown fences, prose, or any extra fields."
    )
    try:
        raw2 = _generate(system, retry_user)
    except OllamaUnavailable as exc:
        log.error("LLM unreachable on retry: %s — escalating", exc)
        return None
    parsed2 = _try_parse(raw2, model_cls)
    if parsed2 is None:
        log.error("LLM second attempt also failed validation. raw=%r", raw2[:300])
    return parsed2


def _try_parse(raw: str, model_cls: type) -> Any | None:
    candidate = _extract_json(raw)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        log.debug("JSON decode failed: %s", exc)
        return None
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        log.debug("Pydantic validation failed: %s", exc)
        return None


def diagnose(
    event: dict[str, Any],
    runbook_hits: list[dict[str, Any]],
    pod_describe: str,
    k8sgpt_findings: list[dict[str, Any]] | None = None,
) -> Diagnosis | None:
    # Plain-text sections — small models like Qwen2.5-3B follow these better
    # than nested JSON, which they tend to echo back instead of summarising.
    fields = event.get("output_fields", {}) or {}
    falco = (
        f"rule: {event.get('rule', '?')}\n"
        f"priority: {event.get('priority', '?')}\n"
        f"output: {event.get('output', '')[:400]}\n"
        f"namespace: {fields.get('k8s.ns.name', '?')}\n"
        f"pod: {fields.get('k8s.pod.name', '?')}\n"
        f"container: {fields.get('container.name', '?')}\n"
        f"process: {fields.get('proc.cmdline', '')[:200]}"
    )
    runbooks_blob = (
        "\n\n".join(f"--- runbook {r['slug']} ---\n{r['text'][:900]}" for r in runbook_hits[:3])
        or "(no runbook matches)"
    )
    pod_blob = (pod_describe or "(no pod describe)")[:1200]
    # K8sGPT block — only included when findings exist, to keep the prompt
    # short on scenarios where k8sgpt has nothing to add.
    findings_blob = ""
    if k8sgpt_findings:
        lines = []
        for f in k8sgpt_findings[:5]:  # cap; k8sgpt can be verbose
            parent = f.get("parent_object") or "(none)"
            lines.append(
                f"- kind={f.get('kind', '?')} name={f.get('name', '?')} "
                f"parent={parent}\n  error: {f.get('error', '')[:200]}"
            )
        findings_blob = "\n\n=== K8SGPT FINDINGS (ground-truth cluster state) ===\n" + "\n".join(
            lines
        )
    user = (
        "=== FALCO ALERT ===\n"
        + falco
        + "\n\n=== RUNBOOK EXCERPTS ===\n"
        + runbooks_blob
        + "\n\n=== POD STATE ===\n"
        + pod_blob
        + findings_blob
        + "\n\n=== YOUR TASK ===\n"
        "Return the diagnosis JSON object now."
    )
    return _call_with_retry(DIAGNOSE_SYSTEM, user, Diagnosis)


def plan_action(
    event: dict[str, Any], diagnosis: dict[str, Any], pod_name: str | None, namespace: str | None
) -> ProposedAction | None:
    fields = event.get("output_fields", {}) or {}
    user = (
        "=== INCIDENT ===\n"
        f"rule: {event.get('rule', '?')}\n"
        f"namespace: {namespace or '?'}\n"
        f"pod: {pod_name or '?'}\n"
        f"deployment: {fields.get('k8s.deployment.name', '?')}\n"
        "\n=== DIAGNOSIS ===\n"
        f"summary: {diagnosis.get('summary', '')}\n"
        f"root_cause: {diagnosis.get('root_cause', '')}\n"
        f"confidence: {diagnosis.get('confidence', 0)}\n"
        "\n=== YOUR TASK ===\n"
        "Choose ONE tool from the catalog and return the plan JSON object now."
    )
    return _call_with_retry(PLAN_ACTION_SYSTEM, user, ProposedAction)
