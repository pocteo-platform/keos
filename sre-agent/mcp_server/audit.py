"""Audit wrapper that adds ``source: "mcp"`` to every MCP-emitted record.

We layer ON TOP of agent.audit.AuditLogger rather than reimplementing it,
so HMAC signing, line format, and verifier all stay identical.
"""

from __future__ import annotations

from typing import Any

from agent.audit import AuditLogger as _BaseLogger
from agent.config import settings


class MCPAuditLogger:
    """Stamps `source: mcp` onto every payload before delegating to the
    standard AuditLogger. Same file, same HMAC key, same line format.
    """

    def __init__(self, path: str | None = None, key: str | None = None) -> None:
        self._inner = _BaseLogger(
            path=path or settings.audit_log_path, key=key or settings.audit_hmac_key
        )

    def emit(self, incident_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        stamped = {**payload, "source": "mcp"}
        return self._inner.emit(incident_id, event_type, stamped)
