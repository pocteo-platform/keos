"""Thin MCP-client wrapper around the K8sGPT `analyze` tool.

K8sGPT's `serve --mcp --mcp-http` exposes its rule-based analyzers over
the Model Context Protocol on http://<host>:8089/mcp (streamable HTTP).
We consume the `analyze` tool only — never with `explain=true`, because we
deliberately do not configure a real LLM backend on the k8sgpt side. The
agent's own Ollama model produces summaries downstream.

K8sGPT's response shape (verified against v0.4.33):
    {
      "status": "ProblemDetected" | "OK",
      "problems": int,
      "results": [
        {
          "kind": str,                         # e.g. "Pod", "Deployment"
          "name": str,                         # "namespace/name"
          "error": [{"Text": str, ...}],       # one or more error strings
          "details": str,
          "parentObject": str | None           # e.g. "Deployment/busybox-bad"
        }, ...
      ]
    }

We flatten that into our `Finding` model so callers don't see k8sgpt
internals.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from fastmcp import Client

from agent.config import settings  # noqa: F401 — re-exported for callers
from agent.schemas import Finding

log = logging.getLogger(__name__)

# Configurable so the api container can point at the k8sgpt service via the
# compose-internal hostname (k8sgpt:8089), while host-side runs default to
# localhost. The graceful-degradation path in analyze() means an
# unreachable k8sgpt never blocks the graph either way.
K8SGPT_URL = os.environ.get("K8SGPT_URL", "http://localhost:8089/mcp")
ANALYZE_TIMEOUT_S = 10.0


def _flatten_results(raw: dict[str, Any]) -> list[Finding]:
    """Turn the k8sgpt JSON response into a list of Finding objects."""
    findings: list[Finding] = []
    for item in raw.get("results", []) or []:
        kind = item.get("kind", "Unknown")
        name = item.get("name", "")
        parent = item.get("parentObject") or None
        # `error` is a list of {"Text": str, ...} — join into one string.
        errors = item.get("error") or []
        msg = " | ".join(e.get("Text", "") if isinstance(e, dict) else str(e) for e in errors)
        findings.append(
            Finding(
                kind=kind,
                name=name,
                error=msg,
                parent_object=parent,
                details=item.get("details", "") or "",
            )
        )
    return findings


async def analyze(
    namespace: str,
    filters: list[str] | None = None,
    url: str = K8SGPT_URL,
    timeout_s: float = ANALYZE_TIMEOUT_S,
) -> list[Finding]:
    """Run k8sgpt analyze on a namespace and return Finding objects.

    Returns [] on connection error, timeout, or malformed response. The agent
    is designed to tolerate k8sgpt being unavailable (graceful degradation),
    so we never raise from here — we log and return empty.
    """
    payload = {"namespace": namespace}
    if filters:
        payload["filters"] = filters

    try:
        async with asyncio.timeout(timeout_s):
            async with Client(url) as c:
                res = await c.call_tool("analyze", payload)
    except (TimeoutError, ConnectionError) as exc:
        log.warning("k8sgpt analyze unreachable (%s); continuing without findings", exc)
        return []
    except Exception as exc:  # noqa: BLE001 — fastmcp + MCP raise a variety of types
        log.warning("k8sgpt analyze failed: %s; continuing without findings", exc)
        return []

    if res.is_error:
        log.warning("k8sgpt analyze returned is_error=true")
        return []

    # K8sGPT returns one TextContent block with the JSON payload as text.
    for block in res.content:
        text = getattr(block, "text", None)
        if not text:
            continue
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            log.warning("k8sgpt response was not valid JSON: %r", text[:200])
            return []
        return _flatten_results(raw)
    return []


def analyze_sync(
    namespace: str,
    filters: list[str] | None = None,
    url: str = K8SGPT_URL,
    timeout_s: float = ANALYZE_TIMEOUT_S,
) -> list[Finding]:
    """Sync convenience wrapper for the synchronous graph nodes."""
    try:
        return asyncio.run(analyze(namespace, filters, url=url, timeout_s=timeout_s))
    except RuntimeError as exc:
        # If an event loop is already running (e.g. inside pytest-asyncio),
        # the caller should use `await analyze(...)` directly.
        log.warning("analyze_sync called from inside an event loop: %s", exc)
        return []
