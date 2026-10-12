"""A failed kubectl action says why, not just its exit code."""

from __future__ import annotations

import pytest

from agent import tools


def test_failed_restart_carries_kubectl_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MCP server and the UI show `error`. A bare `kubectl exit 1` hid
    that the pod to restart was already gone."""
    stderr = 'Error from server (NotFound): pods "falco-46hkw" not found\n'
    monkeypatch.setattr(tools, "_kubectl", lambda _args, timeout=30.0: (1, "", stderr))
    res = tools.restart_pod("falco-46hkw", "falco")
    assert res.success is False
    assert res.error == f"kubectl exit 1: {stderr.strip()}"


def test_kubectl_error_without_stderr_is_the_exit_code() -> None:
    assert tools._kubectl_error(0, "ignored") is None
    assert tools._kubectl_error(1, "  \n") == "kubectl exit 1"
    assert tools._kubectl_error(1, "x" * 400) == "kubectl exit 1: " + "x" * 300 + "..."
