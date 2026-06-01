"""WebSocket multiplexer: surfaces pipeline / falco / cluster / mode / audit
streams to browser clients.

Client protocol (very small):

    -> {"op": "subscribe", "channels": ["pipeline.*", "falco", "mode"]}
    <- {"channel": "pipeline.inc-xxxx", "event": {...}}
    <- {"channel": "falco",            "event": {...}}
    ...
    -> {"op": "unsubscribe", "channels": ["mode"]}
    -> {"op": "ping"}    <- {"op": "pong"}

`pipeline.*` (literal) is a wildcard that receives every incident's
trace events (used by the `/pipeline/live` view). `pipeline.<id>`
subscribes to one specific incident.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from agent import pipeline_trace
from agent.config import settings

log = logging.getLogger(__name__)


class WSConnection:
    """One browser client. Each subscribed channel runs as a background task
    that pushes events into a single send queue; the connection's main loop
    drains the queue and writes to the socket."""

    def __init__(self, ws: WebSocket) -> None:
        self.ws = ws
        self._send_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        self._tasks: dict[str, asyncio.Task] = {}
        self._closed = asyncio.Event()

    async def run(self) -> None:
        """Connection lifecycle: accept, then read commands while a sender
        task drains the queue."""
        await self.ws.accept()
        sender = asyncio.create_task(self._sender())
        try:
            while True:
                raw = await self.ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await self._handle(msg)
        except WebSocketDisconnect:
            pass
        finally:
            self._closed.set()
            for t in self._tasks.values():
                t.cancel()
            sender.cancel()

    async def _sender(self) -> None:
        try:
            while not self._closed.is_set():
                ev = await self._send_q.get()
                await self.ws.send_text(json.dumps(ev, default=str))
        except (WebSocketDisconnect, asyncio.CancelledError):
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("ws sender failed: %s", exc)

    async def _handle(self, msg: dict[str, Any]) -> None:
        op = msg.get("op")
        if op == "ping":
            await self._send_q.put({"op": "pong"})
            return
        if op == "subscribe":
            for ch in msg.get("channels", []):
                self._subscribe(ch)
        elif op == "unsubscribe":
            for ch in msg.get("channels", []):
                self._unsubscribe(ch)

    # ---- channel wiring -------------------------------------------------

    def _subscribe(self, channel: str) -> None:
        if channel in self._tasks:
            return
        task: asyncio.Task | None = None
        if channel.startswith("pipeline."):
            task = asyncio.create_task(self._tail_pipeline(channel))
        elif channel == "falco":
            task = asyncio.create_task(self._tail_nats("infra.events.>", "falco"))
        elif channel == "audit":
            task = asyncio.create_task(self._tail_audit())
        elif channel == "mode":
            task = asyncio.create_task(self._tail_mode())
        elif channel == "cluster":
            task = asyncio.create_task(self._tail_cluster())
        if task is not None:
            self._tasks[channel] = task

    def _unsubscribe(self, channel: str) -> None:
        t = self._tasks.pop(channel, None)
        if t is not None:
            t.cancel()

    # ---- per-channel implementations ------------------------------------

    async def _tail_pipeline(self, channel: str) -> None:
        # "pipeline.*" → global firehose; "pipeline.<id>" → single incident.
        incident_id = None if channel == "pipeline.*" else channel.split(".", 1)[1]
        sub = pipeline_trace.bus.subscribe(incident_id)
        try:
            async for ev in sub:
                await self._send_q.put({"channel": channel, "event": ev})
        except asyncio.CancelledError:
            return
        finally:
            sub.close()

    async def _tail_nats(self, subject: str, channel_name: str) -> None:
        """Subscribe to a NATS subject and forward each message as an event."""
        try:
            import nats
        except ImportError:
            log.warning("nats not available; skipping channel %s", channel_name)
            return
        nc = None
        try:
            nc = await nats.connect(settings.nats_url)

            async def handler(msg) -> None:
                try:
                    body = json.loads(msg.data.decode("utf-8"))
                except json.JSONDecodeError:
                    body = {"_raw": msg.data.decode("utf-8", errors="replace")}
                await self._send_q.put({"channel": channel_name, "event": body})

            await nc.subscribe(subject, cb=handler)
            await self._closed.wait()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("NATS subscription failed (%s); channel=%s", exc, channel_name)
        finally:
            if nc is not None:
                try:
                    await nc.drain()
                except Exception:  # noqa: BLE001
                    pass

    async def _tail_audit(self) -> None:
        """Tail the audit log (forward-only). Polls the file size; cheap on
        an append-only file."""
        path = Path(settings.audit_log_path)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        with path.open("r", encoding="utf-8") as fh:
            fh.seek(0, 2)  # to end
            try:
                while not self._closed.is_set():
                    line = fh.readline()
                    if not line:
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    await self._send_q.put({"channel": "audit", "event": rec})
            except asyncio.CancelledError:
                return

    async def _tail_mode(self) -> None:
        """Poll the ModeStore — its `get()` is O(1) so this is cheap."""
        from agent.mode_store import mode_store

        last: str | None = None
        try:
            while not self._closed.is_set():
                try:
                    cur = mode_store.get()
                except Exception:  # noqa: BLE001
                    cur = None
                if cur != last:
                    last = cur
                    await self._send_q.put({"channel": "mode", "event": {"mode": cur}})
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    async def _tail_cluster(self) -> None:
        """Lightweight cluster-state polling. Emits the workloads-namespace
        pod list every 2s (debounced upstream by the SPA's diff)."""
        from agent import tools as agent_tools

        try:
            while not self._closed.is_set():
                info = agent_tools.kubectl_get("pods", "", "workloads")
                items = info.get("items", []) if isinstance(info, dict) else []
                pods = [_pod_summary(p) for p in items]
                await self._send_q.put(
                    {"channel": "cluster", "event": {"namespace": "workloads", "pods": pods}}
                )
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            return


def _pod_summary(p: dict[str, Any]) -> dict[str, Any]:
    md = p.get("metadata", {}) or {}
    st = p.get("status", {}) or {}
    cs = st.get("containerStatuses", []) or []
    ready = sum(1 for c in cs if c.get("ready"))
    restarts = sum(int(c.get("restartCount", 0) or 0) for c in cs)
    owner = (md.get("ownerReferences") or [{}])[0]
    return {
        "name": md.get("name", ""),
        "namespace": md.get("namespace", ""),
        "phase": st.get("phase", "Unknown"),
        "ready": f"{ready}/{len(cs)}" if cs else "0/0",
        "restarts": restarts,
        "owner_kind": owner.get("kind"),
        "owner_name": owner.get("name"),
    }
