"""Hermetic tests for the chaos_observer event-translation logic.

Phase 14.11 — closes the V1 audit gap that flagged chaos_observer (338
LoC) as having zero tests. We test the PURE bits:

  - REASON_MAP coverage (every kubelet reason the agent claims to
    react to actually maps to a NATS subject)
  - Namespace deny-list semantics
  - Debounce dedup window
  - Rate-limit token bucket
  - Event payload shape (Falco-ish envelope so the rest of the
    graph treats it indistinguishably from synthetic events)

The full integration path (kubernetes-client watch → asyncio.to_thread
→ NATS publish) is exercised by the live `make demo-ui` flow; here we
isolate the deterministic pieces.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from agent.chaos_observer import (
    DEBOUNCE_S,
    RATE_LIMIT_BURST,
    REASON_MAP,
    SYSTEM_NAMESPACES,
    ChaosObserver,
)


# ---------------------------------------------------------------------------
# Reason map
# ---------------------------------------------------------------------------


def test_reason_map_covers_kubelet_lifecycle_reasons() -> None:
    """The map must cover every reason the v1 SRE runbooks promise to
    react to — drift between map and runbook docs is a silent failure
    (kubelet emits the event, agent never wakes)."""
    must_handle = {
        "OOMKilled",
        "BackOff",
        "Killed",
        "FailedScheduling",
        "FailedMount",
        "Unhealthy",
        "ImagePullBackOff",
        "ErrImagePull",
        "NodeNotReady",
        "Evicted",
    }
    assert must_handle <= set(REASON_MAP.keys())


def test_reason_map_priorities_are_well_known() -> None:
    """Falco priority strings the rest of the system understands."""
    allowed = {"Critical", "Error", "Warning", "Info"}
    for _suffix, prio in REASON_MAP.values():
        assert prio in allowed


def test_reason_map_subject_suffixes_are_snake_case() -> None:
    """NATS subjects use snake_case so the consumer's pattern-match
    works (`infra.events.oom_killed` not `infra.events.OOMKilled`)."""
    for suffix, _prio in REASON_MAP.values():
        assert suffix == suffix.lower()
        assert " " not in suffix


# ---------------------------------------------------------------------------
# Namespace filtering
# ---------------------------------------------------------------------------


def test_system_namespaces_includes_kube_system() -> None:
    assert "kube-system" in SYSTEM_NAMESPACES
    assert "kube-public" in SYSTEM_NAMESPACES
    assert "chaos-mesh" in SYSTEM_NAMESPACES


def _fake_event(
    *,
    reason: str = "OOMKilled",
    kind: str = "Pod",
    name: str = "nginx-xxx",
    namespace: str = "workloads",
    message: str = "Container went OOM",
) -> SimpleNamespace:
    """Build a duck-typed object that matches the kubernetes-client
    `CoreV1Event` shape — only the attrs the observer reads."""
    return SimpleNamespace(
        reason=reason,
        message=message,
        involved_object=SimpleNamespace(kind=kind, name=name, namespace=namespace),
        metadata=SimpleNamespace(uid="abcd-1234"),
    )


class _FakeJetStream:
    """Captures every publish so tests can assert on subject + payload
    without a real NATS connection."""

    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes]] = []
        self._loop = None

    async def publish(self, subject: str, payload: bytes) -> None:
        self.publishes.append((subject, payload))


def _wire(observer: ChaosObserver) -> _FakeJetStream:
    """Bypass the real asyncio.run_coroutine_threadsafe + main-loop
    capture by stubbing the publish path. The observer thinks it's
    publishing; we record what it would have sent."""
    js = _FakeJetStream()
    # Loop captured so _handle's `loop is None` check passes.
    observer._loop = SimpleNamespace(_dummy=True)  # type: ignore[assignment]

    import asyncio as _asyncio

    captured: list[tuple[str, bytes]] = []

    def _fake_run_coro_threadsafe(coro, _loop):  # noqa: ANN001
        """Synchronously consume the coroutine, capture the publish args,
        then return an object that satisfies the .result() interface."""
        # Drain the coroutine: it's `js.publish(subject, payload)`.
        # asyncio.run is heavy; just step the coroutine once — the
        # first send drives it into the publish() body which appends.
        try:
            coro.send(None)
        except StopIteration:
            pass
        return SimpleNamespace(result=lambda timeout: None)

    _asyncio.run_coroutine_threadsafe = _fake_run_coro_threadsafe  # type: ignore[assignment]
    js.publishes = captured  # type: ignore[assignment]
    return js


# ---------------------------------------------------------------------------
# _handle: happy path + filters
# ---------------------------------------------------------------------------


def test_handle_publishes_for_known_reason() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # deny-list mode  # type: ignore[attr-defined]
    js = _wire(obs)
    obs._handle(_fake_event(reason="OOMKilled", namespace="workloads"), js)
    assert len(js.publishes) == 1
    subject, payload = js.publishes[0]
    assert subject == "infra.events.oom_killed"
    import json as _json

    body = _json.loads(payload.decode("utf-8"))
    assert body["rule"] == "OOMKilled on Pod/nginx-xxx"
    assert body["priority"] == "Critical"
    assert body["output_fields"]["k8s.ns.name"] == "workloads"


def test_handle_ignores_unmapped_reason() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    obs._handle(_fake_event(reason="Created"), js)
    obs._handle(_fake_event(reason="Pulled"), js)
    assert js.publishes == []


def test_handle_skips_system_namespaces_in_denylist_mode() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # deny-list mode  # type: ignore[attr-defined]
    js = _wire(obs)
    for ns in ("kube-system", "kube-public", "chaos-mesh"):
        obs._handle(_fake_event(namespace=ns), js)
    assert js.publishes == []


def test_handle_allowlist_mode_only_publishes_for_listed_ns() -> None:
    obs = ChaosObserver()
    obs._allow_ns = {"workloads"}  # type: ignore[attr-defined]
    js = _wire(obs)
    obs._handle(_fake_event(namespace="workloads"), js)
    obs._handle(_fake_event(namespace="production"), js)
    obs._handle(_fake_event(namespace="staging"), js)
    assert len(js.publishes) == 1


def test_handle_node_events_pass_through_regardless_of_ns() -> None:
    """NodeNotReady etc. arrive with empty involved_object.namespace
    — they're cluster-scoped. The deny-list shouldn't drop them."""
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    obs._handle(
        _fake_event(reason="NodeNotReady", kind="Node", name="k3d-poc-server-0", namespace=""),
        js,
    )
    assert len(js.publishes) == 1
    assert js.publishes[0][0] == "infra.events.node_not_ready"


def test_handle_missing_involved_object_is_silently_skipped() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    bad = SimpleNamespace(reason="OOMKilled", message="", involved_object=None, metadata=None)
    obs._handle(bad, js)
    assert js.publishes == []


# ---------------------------------------------------------------------------
# Debounce
# ---------------------------------------------------------------------------


def test_debounce_drops_repeat_within_window() -> None:
    """Two identical events fired immediately produce ONE publish."""
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    ev = _fake_event()
    obs._handle(ev, js)
    obs._handle(ev, js)  # same (ns, kind, name, reason) — debounced
    assert len(js.publishes) == 1


def test_debounce_window_constant_is_documented() -> None:
    """The debounce window is documented as 60 s — that constant is
    what users see in the runbooks; a regression that drops it
    silently would re-noise NATS."""
    assert DEBOUNCE_S == 60.0


def test_debounce_distinct_keys_dont_block_each_other() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    obs._handle(_fake_event(name="pod-a"), js)
    obs._handle(_fake_event(name="pod-b"), js)
    obs._handle(_fake_event(reason="Unhealthy"), js)  # different reason
    assert len(js.publishes) == 3


def test_debounce_expires_after_window() -> None:
    obs = ChaosObserver()
    obs._allow_ns = None  # type: ignore[attr-defined]
    js = _wire(obs)
    ev = _fake_event()
    obs._handle(ev, js)
    # Rewind the last-emit timestamp past the debounce window.
    key = ("workloads", "Pod", "nginx-xxx", "OOMKilled")
    obs._last_emit[key] = time.monotonic() - (DEBOUNCE_S + 1)
    obs._handle(ev, js)
    assert len(js.publishes) == 2


# ---------------------------------------------------------------------------
# Rate-limit token bucket
# ---------------------------------------------------------------------------


def test_rate_limit_starts_with_full_burst() -> None:
    obs = ChaosObserver()
    # Should be able to fire `RATE_LIMIT_BURST` consecutive _take_token()
    # calls before refusing.
    granted = sum(1 for _ in range(RATE_LIMIT_BURST) if obs._take_token())
    assert granted == RATE_LIMIT_BURST
    # The next one fails (no refill yet).
    assert obs._take_token() is False


def test_rate_limit_refills_over_time() -> None:
    obs = ChaosObserver()
    # Drain the bucket.
    for _ in range(RATE_LIMIT_BURST):
        obs._take_token()
    assert obs._take_token() is False
    # Advance the refill clock and reset accounting so a token is
    # immediately available.
    obs._last_refill = time.monotonic() - 5.0
    assert obs._take_token() is True


# ---------------------------------------------------------------------------
# Event payload shape
# ---------------------------------------------------------------------------


def test_build_event_envelope_is_falco_shaped() -> None:
    """Triage in graph.py reads `rule`, `priority`, `output`, and
    `output_fields` — break any of these and the existing graph path
    silently drops the event."""
    obs = ChaosObserver()
    payload = obs._build_event(
        _fake_event(reason="OOMKilled"),
        "OOMKilled",
        "Critical",
        "Pod",
        "nginx-xxx",
        "workloads",
    )
    assert payload["rule"] == "OOMKilled on Pod/nginx-xxx"
    assert payload["priority"] == "Critical"
    assert payload["source"] == "kubelet"
    assert "output" in payload
    assert "output_fields" in payload
    of = payload["output_fields"]
    assert of["event.kind"] == "OOMKilled"
    assert of["k8s.ns.name"] == "workloads"
    assert of["k8s.pod.name"] == "nginx-xxx"
    assert of["involved.kind"] == "Pod"


def test_build_event_truncates_message_at_500_chars() -> None:
    obs = ChaosObserver()
    long = "x" * 1000
    payload = obs._build_event(
        _fake_event(message=long),
        "OOMKilled",
        "Critical",
        "Pod",
        "nginx-xxx",
        "workloads",
    )
    assert len(payload["output"]) == 500


def test_build_event_deployment_kind_routes_into_deployment_field() -> None:
    obs = ChaosObserver()
    payload = obs._build_event(
        _fake_event(kind="Deployment", name="nginx-demo"),
        "OOMKilled",
        "Critical",
        "Deployment",
        "nginx-demo",
        "workloads",
    )
    assert payload["output_fields"]["k8s.deployment.name"] == "nginx-demo"
    assert payload["output_fields"]["k8s.pod.name"] == ""
    assert payload["output_fields"]["k8s.node.name"] == ""


def test_build_event_node_kind_routes_into_node_field() -> None:
    obs = ChaosObserver()
    payload = obs._build_event(
        _fake_event(kind="Node", name="k3d-poc-server-0", namespace=""),
        "NodeNotReady",
        "Critical",
        "Node",
        "k3d-poc-server-0",
        "",
    )
    assert payload["output_fields"]["k8s.node.name"] == "k3d-poc-server-0"
    assert payload["output_fields"]["k8s.pod.name"] == ""


def test_observer_singleton_exists() -> None:
    """`agent.serve` imports the module-level singleton; rename it and
    the agent never starts the observer."""
    from agent import chaos_observer

    assert hasattr(chaos_observer, "observer")
    assert isinstance(chaos_observer.observer, ChaosObserver)
