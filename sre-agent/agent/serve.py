"""Single-process orchestrator for `python -m agent.serve`.

Composes (in one asyncio loop):

  * AgentMode CRD watcher (daemon thread, started)
  * Ollama chat-model warmup (best-effort, non-blocking)
  * Pipeline trace bus (attached to this loop)
  * uvicorn serving the FastAPI app (agent/api.py)
  * NATS JetStream subscriber that drives the agent's graph

If any of the agent's external dependencies (NATS, Ollama, K8s API) is
unreachable, the API still comes up — endpoints that need that
dependency will return 503/empty, but the UI's empty states are
designed to render in that case.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

import uvicorn

from agent.api import app as fastapi_app
from agent.config import settings
from agent.graph import build_graph, run_once
from agent.incident_store import save as save_incident
from agent.llm import warmup_chat_model
from agent.logging_utils import configure_logging
from agent.mode_store import mode_store
from agent.pipeline_trace import bus as trace_bus

log = logging.getLogger("serve")


async def _process_message(m, label: str) -> None:
    """Single graph execution for one NATS message. Shared by both
    consumers (user-priority + observer-firehose). Acks the message
    on success, naks on graph crash so JetStream can redeliver."""
    try:
        event = json.loads(m.data.decode("utf-8"))
    except json.JSONDecodeError:
        await m.ack()
        return

    forced_id = event.pop("incident_id", None)
    log.info(
        "[%s] graph run starting incident_id=%s subject=%s", label, forced_id or "(auto)", m.subject
    )
    import time as _time

    started = _time.monotonic()
    final: dict[str, Any] = {}
    _IN_FLIGHT_GAUGE["count"] += 1
    try:
        final = await asyncio.to_thread(_run_with_id, event, forced_id)
        save_incident(final["incident_id"], final)
        trace_bus.mark_done(final["incident_id"])
        # Phase 12 — grade the outcome against the calibration store +
        # emit the Prometheus per-rule bucket counter. Wrapped because
        # calibration must never block the consumer's ack.
        try:
            from agent.calibration import grade_outcome
            from agent.metrics import diagnose_outcome_total

            bucket = grade_outcome(final)
            if bucket and final.get("rule"):
                diagnose_outcome_total.labels(rule=final["rule"], bucket=bucket).inc()
        except Exception:  # noqa: BLE001
            log.exception("[%s] calibration grader raised; ignoring", label)
        log.info("[%s] graph run done incident_id=%s", label, final["incident_id"])
    except Exception:  # noqa: BLE001
        log.exception("[%s] graph crashed for message; nak'ing", label)
        try:
            await m.nak()
        except Exception:  # noqa: BLE001
            pass
        final = {}
    else:
        await m.ack()
    finally:
        _IN_FLIGHT_GAUGE["count"] = max(0, _IN_FLIGHT_GAUGE["count"] - 1)
        elapsed = _time.monotonic() - started
        # Prometheus: record duration + outcome counter. `tool` may be
        # None for short-circuited stale-resolved runs — encode as the
        # literal string "none" so the label space stays bounded.
        try:
            from agent import metrics as _m

            _m.graph_run_duration_seconds.observe(elapsed)
            if final:
                outcome = _classify_outcome(final)
                tool = ((final.get("proposed_action") or {}).get("tool")) or "none"
                domain = final.get("domain") or "sre"
                _m.graph_runs_total.labels(outcome=outcome, tool=tool, domain=domain).inc()
            else:
                _m.graph_runs_total.labels(outcome="crashed", tool="none", domain="sre").inc()
        except Exception:  # noqa: BLE001
            pass


def _classify_outcome(final: dict[str, Any]) -> str:
    """Map a finished IncidentState to one of {healed, escalated,
    auto_resolved, failed}. Used as a low-cardinality Prometheus label."""
    if final.get("stale_resolved"):
        return "auto_resolved"
    if (final.get("verification") or {}).get("healed") is True:
        return "healed"
    if final.get("escalated"):
        return "escalated"
    return "failed"


async def _consumer_loop(sub, label: str, fetch_n: int = 1) -> None:
    """Pull-subscribe loop. Fetches `fetch_n` messages at a time and
    processes them sequentially. Two of these run concurrently — see
    `_nats_consumer()` below. Stops fetching new messages when
    `SHUTDOWN_EVENT` fires; the caller drains the in-flight count
    before terminating the process."""
    while SHUTDOWN_EVENT is None or not SHUTDOWN_EVENT.is_set():
        try:
            msgs = await sub.fetch(fetch_n, timeout=60)
        except Exception:  # noqa: BLE001
            # Timeout / connection blip — sleep briefly and retry.
            await asyncio.sleep(1)
            continue
        for m in msgs:
            await _process_message(m, label)
    # On shutdown, drain whatever this lane started so its ack/nak lands
    # on JetStream before the process exits. obs_wrapper does the same.
    await _drain_in_flight(deadline_s=60.0)


async def _nats_consumer() -> None:
    """Two concurrent JetStream pull subscribers on the INFRA stream:

      * `pocteo-agent-user`  — filter `infra.events.usecase.>`
        Fast lane. User clicks Run on a use case; this consumer picks it
        up immediately even if the observer lane is processing a
        backlog.

      * `pocteo-agent-obs`   — filter `infra.events.*` (NOT usecase.*)
        Slow lane. Kubelet events translated by `chaos_observer` and
        any other auto-generated traffic.

    Both run as asyncio tasks under the same event loop, so they truly
    interleave. Each individual graph run is still serial inside its
    lane (LangGraph + the LLM are not concurrency-safe today).
    """
    try:
        import nats
    except ImportError:
        log.warning("nats-py missing; not starting NATS consumer")
        return

    try:
        nc = await nats.connect(settings.nats_url)
    except Exception as exc:  # noqa: BLE001
        log.warning("NATS unreachable (%s); API will still serve", exc)
        return

    js = nc.jetstream()
    try:
        await js.add_stream(name="INFRA", subjects=["infra.events.>"])
    except Exception:  # noqa: BLE001
        pass

    # CRITICAL: JetStream's default ack_wait is 30s. Our graph takes 60-120s
    # on CPU (two LLM calls at ~30-60s each, plus a 30s verify poll). If
    # ack_wait expires before we ack, JS redelivers the message — the same
    # incident runs again from scratch, never finishes, and the trace file
    # grows by partial duplicate records. Bump ack_wait to 600s (10 min)
    # so even the worst-case slow graph completes before redelivery.
    from nats.js.api import ConsumerConfig

    long_ack = ConsumerConfig(ack_wait=600)  # seconds

    # User lane: messages from `usecase_runner` publish to
    # `infra.events.usecase.<id>`. Filter just those.
    sub_user = await js.pull_subscribe(
        "infra.events.usecase.>",
        durable="pocteo-agent-user",
        config=long_ack,
    )
    log.info(
        "subscribed to infra.events.usecase.> (durable=pocteo-agent-user) — USER lane (ack_wait=600s)"
    )

    # Observer lane: everything else. JetStream pull-subscribe doesn't
    # support exclusion filters directly, so we subscribe to the whole
    # subject space but use a separate durable so this lane's cursor is
    # independent. Messages that match the user lane will be skipped
    # here automatically — JS delivers each message to one consumer's
    # client per durable name, and consumers compete only by filter.
    # Using two distinct durables means each message is delivered to
    # BOTH lanes, but the user lane's filter excludes observer events
    # and the observer lane's logic ignores user events.
    sub_obs = await js.pull_subscribe(
        "infra.events.>",
        durable="pocteo-agent-obs",
        config=long_ack,
    )
    log.info(
        "subscribed to infra.events.> (durable=pocteo-agent-obs) — OBSERVER lane (ack_wait=600s)"
    )

    async def obs_wrapper():
        """Observer lane: skip usecase.* messages (the user lane handles
        them). Without this filter, the same incident would run twice."""
        while SHUTDOWN_EVENT is None or not SHUTDOWN_EVENT.is_set():
            try:
                msgs = await sub_obs.fetch(1, timeout=60)
            except Exception:  # noqa: BLE001
                await asyncio.sleep(1)
                continue
            for m in msgs:
                if m.subject.startswith("infra.events.usecase."):
                    await m.ack()  # leave it to the user lane
                    continue
                await _process_message(m, "obs")
        # Drain in-flight runs the OBS lane started. The USER lane runs
        # its own _consumer_loop which calls this same drain at exit.
        await _drain_in_flight(deadline_s=60.0)

    async def _sample_queue_depth():
        """Periodically refresh the NATS-queue Prometheus gauges so Grafana
        can chart pending+in-flight without needing pull-time consumer
        info calls. 15 s cadence is well below normal scrape intervals."""
        while True:
            try:
                from agent import metrics as _m

                for lane, durable in (
                    ("user", "pocteo-agent-user"),
                    ("observer", "pocteo-agent-obs"),
                ):
                    try:
                        c = await js.consumer_info("INFRA", durable)
                        _m.nats_consumer_pending.labels(lane=lane).set(c.num_pending)
                        _m.nats_consumer_in_flight.labels(lane=lane).set(c.num_ack_pending)
                    except Exception:  # noqa: BLE001 — consumer might not exist yet
                        pass
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(15)

    # Run both lanes concurrently + the queue-depth sampler. Each lane
    # processes one graph run at a time (LangGraph isn't concurrency-safe),
    # but the two lanes interleave so a user click never waits behind
    # observer backlog.
    await asyncio.gather(
        _consumer_loop(sub_user, "user"),
        obs_wrapper(),
        _sample_queue_depth(),
    )


async def _security_consumer() -> None:
    """Phase 21 — the SECOND agent domain's NATS consumer.

    Subscribes to the SECURITY JetStream stream (`security.events.>`) with
    its own durable `keos-security-agent`, independent of the SRE INFRA
    consumers so the two domains never share a cursor. Each message runs
    the SAME graph as the SRE lane — triage reads `source: falco/tetragon`
    (or the explicit `domain: security` tag) and routes the incident down
    the security-domain branches (security prompts + tools + RAG filter).

    Self-disabling: if NATS is unreachable the SRE lanes already log it;
    this lane returns quietly so the API still serves.
    """
    try:
        import nats
    except ImportError:
        return

    try:
        nc = await nats.connect(settings.nats_url)
    except Exception as exc:  # noqa: BLE001
        log.warning("NATS unreachable for security lane (%s); skipping", exc)
        return

    js = nc.jetstream()
    try:
        await js.add_stream(name="SECURITY", subjects=["security.events.>"])
    except Exception:  # noqa: BLE001
        pass

    from nats.js.api import ConsumerConfig

    long_ack = ConsumerConfig(ack_wait=600)  # same 10-min budget as the SRE lane
    sub_sec = await js.pull_subscribe(
        "security.events.>",
        durable="keos-security-agent",
        config=long_ack,
    )
    log.info(
        "subscribed to security.events.> (durable=keos-security-agent) — SECURITY domain (ack_wait=600s)"
    )
    await _consumer_loop(sub_sec, "security")


def _run_with_id(event: dict[str, Any], forced_id: str | None) -> dict[str, Any]:
    """Wrap run_once so we can inject a pinned incident_id from outside."""
    if forced_id:
        # graph._initial_state mints a fresh id; we patch it post-creation
        # by calling build_graph + invoke ourselves.
        from agent.audit import AuditLogger

        graph = build_graph()
        state = {"incident_id": forced_id, "raw_event": event}
        audit = AuditLogger()
        audit.emit(forced_id, "received", {"event_keys": list(event.keys())})
        final = graph.invoke(state)
        audit.emit(
            final["incident_id"],
            "complete",
            {
                "escalated": final.get("escalated", False),
                "healed": (final.get("verification") or {}).get("healed", False),
            },
        )
        return final
    return run_once(event)


async def _serve(host: str, port: int) -> None:
    """Run uvicorn programmatically so it shares our loop."""
    config = uvicorn.Config(
        fastapi_app, host=host, port=port, log_level="info", loop="asyncio", access_log=False
    )
    server = uvicorn.Server(config)
    await server.serve()


# Set when SIGTERM / SIGINT arrives. Long-running loops poll this and
# exit cleanly so the pod can shut down within terminationGracePeriodSeconds.
SHUTDOWN_EVENT: asyncio.Event | None = None

# Tracks how many graph runs are still mid-flight. We drain up to this
# many before exiting on SIGTERM so an in-flight incident's NATS message
# gets ack'd cleanly (vs. redelivered to the next pod replica).
_IN_FLIGHT_GAUGE = {"count": 0}


async def _async_main(host: str, port: int) -> None:
    global SHUTDOWN_EVENT
    SHUTDOWN_EVENT = asyncio.Event()

    # The TraceBus needs to know the loop to do threadsafe-publishes from
    # the sync graph nodes.
    trace_bus.attach_loop(asyncio.get_running_loop())

    # Mode CRD watch is a daemon thread; safe to start without await.
    try:
        mode_store.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("ModeStore failed to start: %s — API will use FALLBACK_AGENT_MODE", exc)

    # Warm the chat model in the background — never block API readiness.
    asyncio.create_task(asyncio.to_thread(warmup_chat_model))

    # Wire signal handlers. SIGTERM is what k8s sends on `kubectl delete
    # pod` / `helm uninstall` / pod eviction. SIGINT is Ctrl-C during local
    # dev. Both flip SHUTDOWN_EVENT; everything else respects it.
    _install_signal_handlers(asyncio.get_running_loop())

    # Start the NATS consumer + the API server in parallel.
    # The chaos observer translates kubelet / Chaos-Mesh events into
    # infra.events.* NATS messages that the same _nats_consumer picks up.
    # Safe to start unconditionally — it self-disables if it can't load
    # kubeconfig or NATS.
    from agent.chaos_observer import observer as chaos_observer

    # Phase 13 — baseline learner. Disabled when PROMETHEUS_URL is
    # empty so an operator can run the agent without Prometheus
    # without seeing a noisy "scrape failed" log every 60 s. Started
    # as a background asyncio task that the gather doesn't wait on,
    # so its lifecycle is independent of the consumer + API.
    if settings.prometheus_url:
        try:
            from agent.baselines.learner import Learner

            _baseline_learner = Learner(
                prometheus_url=settings.prometheus_url,
                scrape_interval_s=settings.baseline_scrape_interval_s,
                fit_interval_s=settings.baseline_fit_interval_s,
            )
            await _baseline_learner.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("baselines: learner failed to start (%s); continuing", exc)

        # Phase 14 — forecast scheduler. Runs only when the baseline
        # learner is enabled (it needs the fitted baselines as input).
        # Same try/except shape — a forecast failure must never break
        # the rest of the agent.
        try:
            from agent.forecasting import scheduler_from_env

            _forecast_scheduler = scheduler_from_env()
            await _forecast_scheduler.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("forecasting: scheduler failed to start (%s); continuing", exc)
    else:
        log.info("baselines: PROMETHEUS_URL empty — learner + forecaster disabled")

    # Phase 15 — GitOps drift detector. Independent of Prometheus;
    # gated on `DRIFT_SOURCE_TYPE` (default `manifests`). Empty /
    # `none` disables the detector cleanly. Failure modes inside
    # the detector are all soft — a missing SoT, unreachable K8s
    # API, or NATS down each become a single WARNING log and the
    # loop keeps going.
    try:
        from agent.drift import detector_from_env

        _drift_detector = detector_from_env()
        if _drift_detector is not None:
            await _drift_detector.start()
        else:
            log.info("drift: DRIFT_SOURCE_TYPE=none — detector disabled")
    except Exception as exc:  # noqa: BLE001
        log.warning("drift: detector failed to start (%s); continuing", exc)

    # Phase 21 — the security domain's telemetry observer. Watches
    # Tetragon TracingPolicy events (surfaced as K8s Events) and
    # normalises them onto security.events.*. Self-disabling like the
    # chaos observer. The Falco path is a webhook (agent/falco_bridge.py
    # mounted on the FastAPI app) — no separate task needed.
    from agent.tetragon_observer import observer as tetragon_observer

    await asyncio.gather(
        _serve(host, port),
        _nats_consumer(),
        _security_consumer(),
        chaos_observer.run(),
        tetragon_observer.run(),
        _retention_loop(),
    )


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Register SIGTERM + SIGINT handlers that initiate graceful shutdown.
    On Windows, `loop.add_signal_handler` is unsupported — fall back to a
    plain `signal.signal` callback. The effect (set the event) is the same."""
    import signal

    def _signal_handler(signum, *_a):
        log.warning(
            "signal %s received — initiating graceful shutdown (in_flight=%d)",
            signum,
            _IN_FLIGHT_GAUGE["count"],
        )
        if SHUTDOWN_EVENT is not None and not SHUTDOWN_EVENT.is_set():
            # Setting the event from a signal handler is async-safe via
            # `call_soon_threadsafe` so the main loop notices on its next tick.
            loop.call_soon_threadsafe(SHUTDOWN_EVENT.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler, int(sig))
        except (NotImplementedError, RuntimeError):
            # Windows + the "main thread only" restriction. Fall back to
            # the plain signal callback — same outcome, just not as clean.
            signal.signal(sig, _signal_handler)


async def _drain_in_flight(deadline_s: float = 60.0) -> None:
    """Wait up to `deadline_s` for all in-flight graph runs to finish.
    Called from the consumer fetch loops when SHUTDOWN_EVENT fires."""
    import time as _time

    started = _time.monotonic()
    while _IN_FLIGHT_GAUGE["count"] > 0:
        remaining = deadline_s - (_time.monotonic() - started)
        if remaining <= 0:
            log.warning(
                "shutdown drain timed out with %d in-flight runs; some "
                "messages may redeliver to the next replica",
                _IN_FLIGHT_GAUGE["count"],
            )
            return
        log.info(
            "shutdown drain: waiting on %d in-flight run(s) (max %.0fs left)",
            _IN_FLIGHT_GAUGE["count"],
            remaining,
        )
        await asyncio.sleep(min(2.0, remaining))
    log.info("shutdown drain: all graph runs completed cleanly")


async def _retention_loop() -> None:
    """Background pruner — runs every hour, sweeps:

      - incident JSON files older than `INCIDENT_RETENTION_DAYS` (default 90)
      - trace JSONL files older than `TRACE_RETENTION_DAYS` (default 30)

    Audit-log rotation is handled by the `TimedRotatingFileHandler` inside
    `AuditLogger`, not here. Setting either env var to 0 disables the
    corresponding sweep (forever-retention).

    Sleeps before the first run so a cold-boot doesn't immediately delete
    incidents that an operator's about to inspect — they get one hour to
    `kubectl cp` before the first pass.
    """
    # Phase C — read retention config from the overlay store on EVERY
    # iteration so the operator's UI edits take effect on the next
    # sweep without a pod restart.
    from agent.retention import get_default_store as _ret_store_get

    store = _ret_store_get()
    cfg = store.get()
    log.info(
        "retention loop: interval=%ds incident_days=%d trace_days=%d audit_days=%d (overlay-aware)",
        cfg.sweep_interval_s,
        cfg.incident_days,
        cfg.trace_days,
        cfg.audit_days,
    )
    # Initial delay before the first sweep — see docstring.
    await asyncio.sleep(cfg.sweep_interval_s)
    while True:
        try:
            cfg = store.get()  # re-read each tick — picks up live UI edits
            # Run the synchronous pruners in a thread so an unexpectedly
            # large delete batch doesn't block the asyncio loop.
            from agent import incident_store as _is

            # 0 means "keep forever" → skip the bucket.
            if cfg.incident_days > 0:
                await asyncio.to_thread(_is.prune_old_incidents, cfg.incident_days)
            if cfg.trace_days > 0:
                await asyncio.to_thread(_is.prune_old_traces, cfg.trace_days)
        except Exception as exc:  # noqa: BLE001 — pruner must never crash serve
            log.warning("retention sweep failed: %s", exc)
        await asyncio.sleep(cfg.sweep_interval_s)


def main() -> int:
    parser = argparse.ArgumentParser(description="self-healing agent + API server")
    # Use KEOS_API_* envs because K8s auto-injects `<SERVICE>_PORT` /
    # `<SERVICE>_HOST` variables into every pod ("API_PORT=tcp://x:8000"),
    # which collides with the plain API_PORT name and crashes int() parse.
    parser.add_argument(
        "--host",
        default=os.environ.get("KEOS_API_HOST", os.environ.get("API_HOST", "0.0.0.0"))
        if not os.environ.get("API_HOST", "").startswith("tcp://")
        else os.environ.get("KEOS_API_HOST", "0.0.0.0"),
    )
    _port_env = os.environ.get("KEOS_API_PORT") or os.environ.get("API_PORT", "8000")
    if _port_env.startswith("tcp://"):
        _port_env = "8000"
    parser.add_argument("--port", type=int, default=int(_port_env))
    args = parser.parse_args()
    configure_logging()
    try:
        asyncio.run(_async_main(args.host, args.port))
    except KeyboardInterrupt:
        log.info("shutdown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
