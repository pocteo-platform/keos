"""Hermetic tests for agent/api.py via FastAPI's TestClient.

External dependencies (kubectl, NATS, K8sGPT, OPA, the LLM) are all
patched out so the suite runs in a few seconds without infrastructure.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _isolate_runtime(monkeypatch, tmp_path):
    """Redirect every Phase 8 runtime path into a per-test tmp dir."""
    audit_log = tmp_path / "audit.log"
    monkeypatch.setattr("agent.config.settings.audit_log_path", str(audit_log), raising=False)
    monkeypatch.setenv("SHP_USECASES_PATH", str(tmp_path / "usecases.json"))
    monkeypatch.setenv("SHP_API_TOKEN", "test-token")
    # Make sure the singleton AuditLogger inside agent.api re-reads settings.
    yield


@pytest.fixture
def client(monkeypatch) -> TestClient:
    # Patch the mode_store singleton used by the API.
    fake_ms = MagicMock()
    fake_ms.get = lambda: "active"
    monkeypatch.setattr("agent.api.mode_store", fake_ms)

    # Patch kubectl JSON shellouts to return controllable shapes.
    def fake_kget(kind, name, namespace):
        if kind == "namespaces":
            return {
                "items": [
                    {"metadata": {"name": "workloads", "creationTimestamp": "2026-01-01T00:00:00Z"}}
                ]
            }
        if kind == "nodes":
            return {
                "items": [
                    {
                        "metadata": {"name": "k3d-poc-server-0"},
                        "status": {
                            "nodeInfo": {"kubeletVersion": "v1.31.5-k3s1"},
                            "conditions": [{"type": "Ready", "status": "True"}],
                        },
                        "spec": {},
                    },
                ]
            }
        if kind == "pods":
            return {
                "items": [
                    {
                        "metadata": {
                            "name": "nginx-demo-abc",
                            "namespace": namespace or "workloads",
                            "creationTimestamp": "2026-01-01T00:00:00Z",
                        },
                        "status": {
                            "phase": "Running",
                            "containerStatuses": [
                                {"ready": True, "restartCount": 0},
                            ],
                        },
                    },
                ]
            }
        return {}

    monkeypatch.setattr("agent.api._kubectl_get", fake_kget)

    from agent.api import app

    return TestClient(app)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_health_does_not_require_auth(client) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_other_endpoints_require_auth(client) -> None:
    r = client.get("/api/mode")
    assert r.status_code == 401


def test_invalid_token_is_rejected(client) -> None:
    r = client.get("/api/mode", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


def test_get_mode(client) -> None:
    r = client.get("/api/mode", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["mode"] == "active"


# ---------------------------------------------------------------------------
# Cluster
# ---------------------------------------------------------------------------


def test_list_nodes(client) -> None:
    r = client.get("/api/cluster/nodes", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    nodes = r.json()
    assert len(nodes) == 1
    assert nodes[0]["ready"] is True


def test_list_namespaces(client) -> None:
    r = client.get("/api/cluster/namespaces", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    names = [n["name"] for n in r.json()]
    assert "workloads" in names


def test_list_pods(client) -> None:
    r = client.get("/api/cluster/pods?ns=workloads", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    pods = r.json()
    assert len(pods) == 1
    assert pods[0]["phase"] == "Running"


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------


def test_list_incidents_empty(client) -> None:
    r = client.get("/api/incidents", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_get_incident_404(client) -> None:
    r = client.get("/api/incidents/nonexistent", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 404


def test_incidents_round_trip(client, monkeypatch) -> None:
    from agent import incident_store

    incident_store.save(
        "inc-test",
        {
            "incident_id": "inc-test",
            "rule": "Test rule",
            "severity": "high",
            "namespace": "workloads",
            "pod": "nginx-demo-abc",
            "raw_event": {"rule": "Test rule"},
            "diagnosis": {
                "summary": "test",
                "root_cause": "test",
                "confidence": 0.9,
                "evidence": [],
            },
            "proposed_action": {
                "tool": "restart_pod",
                "args": {"pod_name": "x"},
                "scope": "pod",
                "reversible": True,
                "rationale": "test",
            },
            "policy_decision": {"allow": True, "reasons": []},
            "verification": {"healed": True, "checks": []},
            "escalated": False,
        },
    )
    r = client.get("/api/incidents", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r = client.get("/api/incidents/inc-test", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    body = r.json()
    assert body["incident_id"] == "inc-test"
    assert body["proposed_action"]["tool"] == "restart_pod"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def test_audit_empty(client) -> None:
    r = client.get("/api/audit", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_audit_verify_empty(client) -> None:
    r = client.post("/api/audit/verify", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    # Empty log -> n=0 -> valid is False (no records to be valid about); endpoint still returns 200.
    assert isinstance(r.json()["valid"], bool)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_list_scenarios(client, monkeypatch) -> None:
    # build_event resolves a real pod via subprocess; stub that out.
    monkeypatch.setattr(
        "agent.synthetic_events._resolve_pod_name", lambda ns, label, fallback: fallback
    )
    r = client.get("/api/scenarios", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 6  # the six we defined in Phase 7.2
    ids = {s["id"] for s in items}
    assert 1 in ids and 6 in ids


def test_run_scenario_unknown_id(client) -> None:
    r = client.post(
        "/api/scenarios/run",
        headers={"Authorization": "Bearer test-token"},
        json={"scenario_id": 999},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


def test_usecases_seed_library(client) -> None:
    r = client.get("/api/usecases", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    items = r.json()
    # The library starter set has 3 entries.
    assert len(items) == 3
    names = {i["id"] for i in items}
    assert "lib-memory-leak" in names


def test_usecase_create_and_delete(client) -> None:
    body = {
        "name": "my custom case",
        "description_en": "EN desc",
        "description_fr": "FR desc",
        "tags": ["custom"],
        "target": {"namespace": "workloads", "owner_kind": "Deployment"},
        "falco": {"rule": "Test rule", "priority": "Warning"},
        "expectation": {},
    }
    r = client.post("/api/usecases", headers={"Authorization": "Bearer test-token"}, json=body)
    assert r.status_code == 200
    uc = r.json()
    uc_id = uc["id"]
    # delete
    r = client.delete(f"/api/usecases/{uc_id}", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    r = client.delete(f"/api/usecases/{uc_id}", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Approvals
# ---------------------------------------------------------------------------


def test_list_approvals_empty(client) -> None:
    r = client.get("/api/approvals", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json() == []


def test_approve_missing(client) -> None:
    r = client.post(
        "/api/actions/approve",
        headers={"Authorization": "Bearer test-token"},
        json={"incident_id": "no-such-id"},
    )
    assert r.status_code == 200
    assert r.json()["approved"] is False


def test_deny_missing(client) -> None:
    r = client.post(
        "/api/actions/deny",
        headers={"Authorization": "Bearer test-token"},
        json={"incident_id": "no-such-id", "reason": "noop"},
    )
    assert r.status_code == 200
    assert r.json()["approved"] is False
