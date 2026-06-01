"""Hermetic tests for agent/k8sgpt_client.py.

We patch fastmcp.Client with an async-context-manager mock that returns a
controllable CallToolResult. No k8sgpt container required.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.k8sgpt_client import analyze


def _mock_text_block(text: str) -> SimpleNamespace:
    return SimpleNamespace(text=text)


def _mock_result(text: str | None, is_error: bool = False) -> SimpleNamespace:
    content = [_mock_text_block(text)] if text is not None else []
    return SimpleNamespace(content=content, is_error=is_error)


def _client_mock(
    call_result: Any | None = None,
    connect_exc: Exception | None = None,
    call_exc: Exception | None = None,
) -> MagicMock:
    """Build a MagicMock that mimics `async with Client(url) as c: c.call_tool(...)`."""
    client = MagicMock()
    instance = MagicMock()
    if call_exc is not None:
        instance.call_tool = AsyncMock(side_effect=call_exc)
    else:
        instance.call_tool = AsyncMock(return_value=call_result)

    if connect_exc is not None:
        client.return_value.__aenter__ = AsyncMock(side_effect=connect_exc)
    else:
        client.return_value.__aenter__ = AsyncMock(return_value=instance)
    client.return_value.__aexit__ = AsyncMock(return_value=False)
    return client


@pytest.mark.asyncio
async def test_analyze_success_with_one_finding() -> None:
    """A populated k8sgpt response flattens into Finding objects."""
    payload = (
        '{"status":"ProblemDetected","problems":1,"results":[{'
        '"kind":"Pod","name":"workloads/busybox-bad-abc",'
        '"error":[{"Text":"the termination reason is Error exitCode=1"}],'
        '"details":"","parentObject":"Deployment/busybox-bad"}]}'
    )
    mock_client = _client_mock(call_result=_mock_result(payload))
    with patch("agent.k8sgpt_client.Client", mock_client, create=True):
        findings = await analyze("workloads", ["Pod"])
    assert len(findings) == 1
    f = findings[0]
    assert f.kind == "Pod"
    assert f.name == "workloads/busybox-bad-abc"
    assert "exitCode=1" in f.error
    assert f.parent_object == "Deployment/busybox-bad"


@pytest.mark.asyncio
async def test_analyze_returns_empty_when_no_problems() -> None:
    """K8sGPT returns `results:null` or `[]` on healthy namespaces."""
    payload = '{"status":"OK","problems":0,"results":null}'
    mock_client = _client_mock(call_result=_mock_result(payload))
    with patch("agent.k8sgpt_client.Client", mock_client, create=True):
        findings = await analyze("workloads")
    assert findings == []


@pytest.mark.asyncio
async def test_analyze_returns_empty_on_connection_error() -> None:
    """k8sgpt down -> graph continues with empty findings, never raises."""
    mock_client = _client_mock(connect_exc=ConnectionError("connection refused"))
    with patch("agent.k8sgpt_client.Client", mock_client, create=True):
        findings = await analyze("workloads")
    assert findings == []


@pytest.mark.asyncio
async def test_analyze_returns_empty_on_malformed_json() -> None:
    """If k8sgpt returns non-JSON text, we log + return empty."""
    mock_client = _client_mock(call_result=_mock_result("<html>500 internal</html>"))
    with patch("agent.k8sgpt_client.Client", mock_client, create=True):
        findings = await analyze("workloads")
    assert findings == []


@pytest.mark.asyncio
async def test_analyze_returns_empty_on_tool_error() -> None:
    """If the MCP tool itself reports is_error, we treat it as no findings."""
    mock_client = _client_mock(call_result=_mock_result("doesn't matter", is_error=True))
    with patch("agent.k8sgpt_client.Client", mock_client, create=True):
        findings = await analyze("workloads")
    assert findings == []
