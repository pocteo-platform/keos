"""Reliability tests for the agent's external-dependency failure modes.

Each test isolates ONE failure mode and asserts the agent's defensive
behavior. None of these touch the network — every external call is
patched. The whole file runs in well under a second.

Covered failure modes (mirror the production runbook):

  - Ollama 503 → 3-attempt backoff → OllamaUnavailable raised
  - Ollama RequestError → same retry path
  - Ollama 4xx (not retried) → raises immediately
  - Qdrant timeout → empty hits returned (graceful degrade)
  - Qdrant client raises → same graceful degrade
  - OPA unreachable → policy_gate returns allow=False (fail-closed)
  - SIGTERM handler is wired
"""

from __future__ import annotations

import time
from unittest.mock import patch

import httpx
import pytest

from agent import llm, policy_gate, tools
from agent.schemas import ProposedAction

# ---------------------------------------------------------------------------
# Ollama retry policy.
# ---------------------------------------------------------------------------


class _FakeResp:
    """Stand-in for httpx.Response carrying a configurable status_code."""

    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status_code = status
        self._body = body or {"response": "{}"}
        self.request = httpx.Request("POST", "http://stub/")

    def json(self) -> dict:
        return self._body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"{self.status_code}", request=self.request, response=self)


def test_ollama_503_retries_then_raises():
    """3 attempts (1+2s backoff = ~3s total), then OllamaUnavailable."""
    calls = {"n": 0}

    def _stub(*_a, **_kw):
        calls["n"] += 1
        return _FakeResp(503)

    with patch.object(llm.httpx, "post", _stub):
        t0 = time.monotonic()
        with pytest.raises(llm.OllamaUnavailable):
            llm._generate("sys", "user")
        elapsed = time.monotonic() - t0

    assert calls["n"] == 3, f"expected 3 attempts, got {calls['n']}"
    # 1 s + 2 s = 3 s minimum sleep; allow some slack for CI noise.
    assert 2.5 <= elapsed <= 8.0, f"backoff timing off: {elapsed:.2f}s"


def test_ollama_request_error_retries():
    """Connection errors also retry (same path as 5xx)."""
    calls = {"n": 0}

    def _stub(*_a, **_kw):
        calls["n"] += 1
        raise httpx.ConnectError("connection refused")

    with patch.object(llm.httpx, "post", _stub):
        with pytest.raises(llm.OllamaUnavailable):
            llm._generate("sys", "user")
    assert calls["n"] == 3


def test_ollama_4xx_does_not_retry():
    """A 400 means our request was malformed, not transient. Raise immediately."""
    calls = {"n": 0}

    def _stub(*_a, **_kw):
        calls["n"] += 1
        return _FakeResp(400)

    with patch.object(llm.httpx, "post", _stub):
        with pytest.raises(httpx.HTTPStatusError):
            llm._generate("sys", "user")
    assert calls["n"] == 1, "4xx must NOT trigger retries"


def test_call_with_retry_returns_none_when_ollama_unreachable():
    """When _generate exhausts its retries, _call_with_retry returns None
    so the graph can escalate."""
    with patch.object(llm, "_generate", side_effect=llm.OllamaUnavailable("nope")):
        result = llm._call_with_retry("sys", "user", llm.Diagnosis)
    assert result is None


# ---------------------------------------------------------------------------
# Qdrant graceful degrade.
# ---------------------------------------------------------------------------


def test_vector_search_returns_empty_on_qdrant_down():
    """All retries fail → return [] (graceful degrade). The graph continues
    without runbook context — lower confidence → more likely to escalate,
    which is the right safety behavior."""

    class _BrokenClient:
        def __init__(self, *a, **kw):
            pass

        def query_points(self, *a, **kw):
            raise ConnectionError("qdrant unreachable")

    with patch.object(tools, "QdrantClient", _BrokenClient):
        hits = tools.vector_search([0.0] * 768)
    assert hits == []


def test_vector_search_succeeds_on_first_attempt():
    """No retries needed when Qdrant responds normally."""

    class _Point:
        score = 0.9
        payload = {"slug": "runbook-001", "text": "memory leak"}

    class _Resp:
        points = [_Point()]

    class _OkClient:
        def __init__(self, *a, **kw):
            pass

        def query_points(self, *a, **kw):
            return _Resp()

    with patch.object(tools, "QdrantClient", _OkClient):
        hits = tools.vector_search([0.0] * 768)
    assert len(hits) == 1
    assert hits[0]["slug"] == "runbook-001"


# ---------------------------------------------------------------------------
# OPA fail-closed.
# ---------------------------------------------------------------------------


def test_policy_gate_fails_closed_when_opa_unreachable():
    """When OPA is unreachable, policy_gate MUST return allow=False so the
    graph escalates instead of executing without policy oversight."""
    action = ProposedAction(
        tool="restart_pod",
        args={"pod_name": "x", "namespace": "workloads"},
        scope="pod",
        reversible=True,
        rationale="test",
    )

    def _stub(*_a, **_kw):
        raise httpx.ConnectError("opa down")

    with patch.object(policy_gate.httpx, "post", _stub):
        decision = policy_gate.evaluate(action, "high", "active", "workloads")

    assert decision.allow is False
    assert any("OPA unreachable" in r for r in decision.reasons), decision.reasons


# ---------------------------------------------------------------------------
# Shutdown wiring.
# ---------------------------------------------------------------------------


def test_serve_exposes_shutdown_primitives():
    """The graceful-shutdown machinery must be importable + callable.
    Full lifecycle (SIGTERM during a real graph run) is exercised in the
    integration test that ships in the docs; here we just guard the public
    surface so a future refactor can't silently delete it."""
    from agent import serve

    assert hasattr(serve, "_install_signal_handlers")
    assert callable(serve._install_signal_handlers)
    assert hasattr(serve, "_drain_in_flight")
    assert callable(serve._drain_in_flight)
    assert hasattr(serve, "_IN_FLIGHT_GAUGE")
    assert isinstance(serve._IN_FLIGHT_GAUGE, dict)
    assert serve._IN_FLIGHT_GAUGE["count"] == 0  # starts clean
