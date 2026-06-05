"""Live reader for the AgentMode CRD.

Spawns a daemon thread that watches the `agentmodes.security.poc` cluster-scoped
resource and updates an in-process `current` field. Other code reads
`get_mode()` without touching the API server.

Why a watch instead of a per-event GET? The graph already eats a few hundred
ms per event on LLM calls — adding another 50-100 ms for a kubectl call would
make `make demo` feel laggy. A watch costs one long-poll connection and gives
us reads in ~microseconds.

Reconnect behaviour: kubernetes-client raises ApiException when the watch
times out / disconnects. We re-enter the watch loop with the last seen
resourceVersion. If the API server is unavailable, we keep the LAST known
mode rather than failing closed — failing-closed here would mean the agent
stops processing events the moment the CRD watch hiccups. The fail-closed
guarantee lives in OPA (which always re-checks `agent_mode != "active"`).
"""

from __future__ import annotations

import logging
import threading

from kubernetes import client, config, watch

from agent.config import settings

log = logging.getLogger(__name__)

CRD_GROUP = "security.poc"
CRD_VERSION = "v1"
CRD_PLURAL = "agentmodes"

_DEFAULT_MODE = "observe-only"


class ModeStore:
    """In-process cache of the AgentMode CR's `spec.mode`."""

    def __init__(self) -> None:
        self._mode: str = _DEFAULT_MODE
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    # ---- public API ------------------------------------------------------

    def start(self) -> None:
        """Kick off the watch thread. Blocks up to 5s for the initial read so
        callers get a real value, not the default."""
        if self._thread is not None:
            return
        # Try in-cluster auth first (production: agent runs as a Pod with a
        # projected SA token), fall back to the dev kubeconfig file.
        # Without this fallback, the watcher silently runs against no
        # cluster, never observes a CRD change, and /api/mode keeps
        # returning the boot default even after the AgentMode CR is
        # patched (so the SPA's mode toggle appears to do nothing).
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config(config_file=settings.kubeconfig)
        self._thread = threading.Thread(target=self._run, name="agent-mode-watcher", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            log.warning("ModeStore initial fetch timed out; using default %r", self._mode)

    def stop(self) -> None:
        self._stop.set()

    def get(self) -> str:
        with self._lock:
            return self._mode

    # ---- internals -------------------------------------------------------

    def _run(self) -> None:
        """Bulletproof watch loop.

        Earlier incident: k3d's load balancer dropped a long-lived watch
        connection mid-flight with an SSL EOF. urllib3 raised MaxRetryError,
        which is NOT a kubernetes.client.ApiException, so the previous
        narrow `except ApiException:` let the error propagate out of the
        thread — killing it permanently. From that point on the API kept
        reporting whatever the last-cached mode was, ignoring every CR
        patch.

        Three defenses now in place:
          1. Initial GET is in its own retry-with-backoff loop instead of a
             one-shot try.
          2. The watch's `for ev in w.stream(...)` is wrapped in a bare
             `except Exception` (already was), so any transport error
             triggers a 5 s backoff + reconnect — same flow as a normal
             watch timeout.
          3. The ENTIRE body is wrapped in a top-level
             `while not self._stop` keep-alive: even an unexpected
             exception during reconnect setup just bounces back to the top
             of the outer loop instead of leaking out and killing the
             thread.
        """
        api = client.CustomObjectsApi()
        backoff = 1.0  # seconds, exponential up to 30
        first_pass = True

        while not self._stop.is_set():
            # ---- Initial GET: keep retrying until success OR thread stops.
            #      Always fires the readiness event after the first attempt
            #      so callers don't block forever on a busted API server.
            try:
                obj = api.get_cluster_custom_object(
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    plural=CRD_PLURAL,
                    name=settings.agent_mode_name,
                )
                self._apply(obj)
                backoff = 1.0  # reset on success
            except Exception as exc:  # noqa: BLE001 — broad catch is the point
                log.warning(
                    "AgentMode GET failed (%s); retrying in %.1fs",
                    type(exc).__name__,
                    backoff,
                )
                if first_pass:
                    self._ready.set()  # don't block start() forever
                    first_pass = False
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)
                continue
            if first_pass:
                self._ready.set()
                first_pass = False

            # ---- Watch loop. On any failure, fall through to outer loop
            #      which does another GET + reset.
            w = watch.Watch()
            try:
                for ev in w.stream(
                    api.list_cluster_custom_object,
                    group=CRD_GROUP,
                    version=CRD_VERSION,
                    plural=CRD_PLURAL,
                    timeout_seconds=300,
                ):
                    if self._stop.is_set():
                        w.stop()
                        return
                    obj = ev.get("object", {})
                    if obj.get("metadata", {}).get("name") != settings.agent_mode_name:
                        continue
                    self._apply(obj)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "AgentMode watch error (%s); reconnecting in %.1fs",
                    type(exc).__name__,
                    backoff,
                )
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 30.0)
            else:
                # Stream ended cleanly (timeout). Reconnect immediately.
                pass

    def _apply(self, obj: dict) -> None:
        mode = obj.get("spec", {}).get("mode")
        if not isinstance(mode, str):
            return
        with self._lock:
            if mode != self._mode:
                log.info("AgentMode changed: %s -> %s", self._mode, mode)
                self._mode = mode


# Module-level singleton: anyone can `from agent.mode_store import mode_store`.
mode_store = ModeStore()
