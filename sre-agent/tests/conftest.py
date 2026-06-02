"""Shared fixtures for the agent test suite.

The policy tests in test_policy.py talk to a live OPA container (integration).
test_graph.py is fully hermetic: Ollama, OPA, Qdrant, and kubectl are all
mocked so the tests run in ~1 second without any infrastructure up.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Canned LLM responses keyed by scenario id. Each is a (diagnosis, plan) pair
# that the mock Ollama endpoint will return in order.
# ---------------------------------------------------------------------------

CANNED_LLM: dict[int, dict[str, dict[str, Any]]] = {
    1: {
        "diagnosis": {
            "summary": "Container memory is climbing rapidly post-deploy.",
            "root_cause": "A regression in the latest rollout introduced a memory leak.",
            "confidence": 0.85,
            "evidence": ["event:growth_rate=18MiB/min", "runbook 001 match"],
        },
        "plan": {
            "tool": "rollout_undo",
            "args": {"deployment_name": "nginx-demo", "namespace": "workloads"},
            "scope": "deploy",
            "reversible": True,
            "rationale": "Revert to the previous ReplicaSet whose memory was stable.",
        },
    },
    2: {
        "diagnosis": {
            "summary": "Pod has crashed 5 times in 3 minutes (CrashLoopBackOff).",
            "root_cause": "Recent Deployment rollout introduced a bad command/config.",
            "confidence": 0.78,
            "evidence": ["event:restart.count=5", "event:exit.code=1", "runbook 002 match"],
        },
        "plan": {
            "tool": "rollout_undo",
            "args": {"deployment_name": "busybox-bad", "namespace": "workloads"},
            "scope": "deploy",
            "reversible": True,
            "rationale": "Revert to the previous good ReplicaSet.",
        },
    },
    3: {
        "diagnosis": {
            "summary": "Container image pull failed (ErrImagePull).",
            "root_cause": "The Deployment references a non-existent tag.",
            "confidence": 0.9,
            "evidence": ["event:reason=ImagePullBackOff", "runbook 003 match"],
        },
        "plan": {
            "tool": "rollout_undo",
            "args": {"deployment_name": "broken-image", "namespace": "workloads"},
            "scope": "deploy",
            "reversible": True,
            "rationale": "Revert to the previous revision whose image was pullable.",
        },
    },
    4: {
        "diagnosis": {
            "summary": "Node reports MemoryPressure=True; pods at risk of eviction.",
            "root_cause": "A noisy-neighbour workload is consuming more memory than its requests reserve.",
            "confidence": 0.82,
            "evidence": ["event:kubelet.reason=MemoryPressure", "runbook 004 match"],
        },
        "plan": {
            "tool": "cordon_node",
            "args": {"node_name": "k3d-poc-server-0"},
            "scope": "node",
            "reversible": True,
            "rationale": "Stop new scheduling on the pressured node; drain follow-up reserved for higher severity.",
        },
    },
    5: {
        "diagnosis": {
            "summary": "Deployment rollout stalled: 0 of 3 new replicas Ready.",
            "root_cause": "New ReplicaSet is failing readiness on its current image.",
            "confidence": 0.88,
            "evidence": ["event:DeploymentRolloutStalled", "runbook 005 match"],
        },
        "plan": {
            "tool": "rollout_undo",
            "args": {"deployment_name": "nginx-demo", "namespace": "workloads"},
            "scope": "deploy",
            "reversible": True,
            "rationale": "Revert to the prior ReplicaSet to restore availability.",
        },
    },
    # Scenario 6 — config drift detected by GitOps controller.
    6: {
        "diagnosis": {
            "summary": "ConfigMap workloads/app-config diverged from Git.",
            "root_cause": "A manual `kubectl edit` during an incident was never back-ported.",
            "confidence": 0.7,
            "evidence": ["event:OutOfSync", "runbook 006 covers drift"],
            "notes": "",
        },
        "plan": {
            "tool": "page_human",
            "args": {"reason": "drift on ConfigMap requires operator review"},
            "scope": "n/a",
            "reversible": True,
            "rationale": "Drift on non-rollback-safe resources should be operator-decided.",
        },
    },
    # Scenario 7 — traffic surge; HPA lagging.
    7: {
        "diagnosis": {
            "summary": "3 of 8 nginx-demo replicas at 95%+ CPU; HPA lagging.",
            "root_cause": "Legitimate traffic surge faster than HPA can react.",
            "confidence": 0.86,
            "evidence": ["event:HPALagging", "runbook 007 match"],
        },
        "plan": {
            "tool": "scale_deployment",
            "args": {"deployment_name": "nginx-demo", "namespace": "workloads", "replicas": 8},
            "scope": "deploy",
            "reversible": True,
            "rationale": "Bump replicas ahead of HPA; it will settle back to its computed value.",
        },
    },
    # Special: a low-confidence diagnosis should route directly to escalate
    # without even calling plan_action.
    "low_conf": {
        "diagnosis": {
            "summary": "Ambiguous event; could be benign or malicious.",
            "root_cause": "Insufficient evidence.",
            "confidence": 0.4,
            "evidence": ["no runbook clearly matches"],
        },
        "plan": {  # will never be consumed but included for symmetry
            "tool": "page_human",
            "args": {"reason": "low confidence"},
            "scope": "n/a",
            "reversible": True,
            "rationale": "Defer to operator.",
        },
    },
}


# ---------------------------------------------------------------------------
# Mock kubectl / subprocess plumbing. Tests register expected responses by
# (verb, kind, name) so we can simulate get/describe/delete/apply outcomes.
# ---------------------------------------------------------------------------


class FakeKubectl:
    """Minimal canned-response store for subprocess.run calls."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        # Default behaviour: every command succeeds with empty stdout.
        self.default_rc = 0
        self._responses: dict[tuple[str, ...], tuple[int, str, str]] = {}

    def register(
        self, key: tuple[str, ...], rc: int = 0, stdout: str = "", stderr: str = ""
    ) -> None:
        self._responses[key] = (rc, stdout, stderr)

    def __call__(self, cmd: list[str], **_kw: Any) -> Any:
        self.calls.append(cmd)
        # Find a registered response whose key is a subsequence of cmd.
        for key, resp in self._responses.items():
            if all(k in cmd for k in key):
                rc, out, err = resp
                return _CompletedProcess(rc, out, err)
        return _CompletedProcess(self.default_rc, "", "")


class _CompletedProcess:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# Pytest fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_kubectl(monkeypatch):
    """Replace subprocess.run inside agent.tools with a controllable mock."""
    fk = FakeKubectl()
    # Default: nginx Deployment fully ready, so verify -> healed=true.
    fk.register(
        ("get", "deployment"), stdout=json.dumps({"status": {"readyReplicas": 3, "replicas": 3}})
    )
    fk.register(
        ("get", "pods", "-o", "json"),
        stdout=json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "nginx-demo-abc"},
                        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
                    }
                ]
            }
        ),
    )
    fk.register(
        ("get", "pod"),
        stdout=json.dumps(
            {
                "metadata": {"labels": {"app": "nginx-demo"}},
            }
        ),
    )
    fk.register(("describe", "pod"), stdout="Name: nginx-demo-abc\nStatus: Running\n")
    fk.register(("delete", "pod"), stdout='pod "nginx-demo-abc" deleted')
    fk.register(("rollout", "undo"), stdout="deployment.apps/X rolled back")
    fk.register(("apply", "-f", "-"), stdout="resource configured")
    # SRE catalog (Phase 9.2): scale / cordon / drain.
    fk.register(("scale",), stdout="deployment.apps/nginx-demo scaled")
    fk.register(("cordon",), stdout="node/k3d-poc-server-0 cordoned")
    fk.register(("drain",), stdout="node/k3d-poc-server-0 drained")

    import agent.tools as t

    monkeypatch.setattr(t.subprocess, "run", fk)
    return fk


@pytest.fixture
def fake_qdrant(monkeypatch):
    """Stub Qdrant: vector_search returns one canned runbook hit."""
    import agent.tools as t

    def fake_search(vector, top_k=3):
        return [
            {
                "slug": "001-shell-in-container",
                "text": "Shell in container runbook excerpt...",
                "score": 0.91,
            }
        ]

    monkeypatch.setattr(t, "vector_search", fake_search)
    monkeypatch.setattr(t, "embed_text", lambda text: [0.1] * 768)


@pytest.fixture
def fake_mode_store(monkeypatch):
    """ModeStore that returns whatever the test set. Default = 'active'."""

    class FakeStore:
        def __init__(self) -> None:
            self.mode = "active"

        def start(self) -> None:
            pass

        def get(self) -> str:
            return self.mode

    fs = FakeStore()
    import agent.graph as g

    monkeypatch.setattr(g, "mode_store", fs)
    return fs


@pytest.fixture(autouse=True)
def mcp_bypass(monkeypatch):
    """Force agent.graph's `node_execute` to use direct dispatch instead of
    HTTP-calling the MCP server. Test_mcp.py spins up the FastMCP in-process;
    test_graph.py exercises the agent in isolation. Neither needs an HTTP
    server running."""
    monkeypatch.setenv("MCP_BYPASS", "1")


@pytest.fixture
def fake_k8sgpt(monkeypatch):
    """Stub the graph's k8sgpt call. Default = no findings.

    Tests can set `fixture.findings = [...]` to inject a canned list that
    will be returned to `node_k8sgpt_analyze`.
    """
    from agent.schemas import Finding

    class Stub:
        findings: list[Finding] = []

    stub = Stub()

    def fake_sync(namespace, filters=None):
        return list(stub.findings)

    import agent.graph as g

    monkeypatch.setattr(g, "k8sgpt_analyze_sync", fake_sync)
    return stub


@pytest.fixture
def patch_warmup(monkeypatch):
    """Skip the model warmup call so tests run instantly."""
    import agent.graph as g

    monkeypatch.setattr(g, "warmup_chat_model", lambda: None)


@pytest.fixture
def patch_llm(monkeypatch):
    """Inject canned LLM responses by patching the llm.diagnose / plan_action
    functions directly. Simpler than mocking httpx for every test."""
    from agent.schemas import Diagnosis, ProposedAction

    state = {"scenario": 1}

    def set_scenario(key):
        state["scenario"] = key

    def fake_diagnose(event, runbook_hits, pod_describe, k8sgpt_findings=None, baselines=None):
        canned = CANNED_LLM[state["scenario"]]["diagnosis"]
        return Diagnosis.model_validate(canned)

    def fake_plan(event, diagnosis, pod_name, namespace):
        canned = CANNED_LLM[state["scenario"]]["plan"]
        return ProposedAction.model_validate(canned)

    import agent.graph as g

    monkeypatch.setattr(g, "llm_diagnose", fake_diagnose)
    monkeypatch.setattr(g, "llm_plan_action", fake_plan)
    return set_scenario


@pytest.fixture
def patch_opa(monkeypatch):
    """Mock the OPA HTTP call. Default: ALLOW. Test can override via set()."""
    from agent.schemas import PolicyDecision

    state = {"decision": PolicyDecision(allow=True, reasons=[])}

    def set_decision(allow: bool, reasons: list[str] | None = None):
        state["decision"] = PolicyDecision(allow=allow, reasons=reasons or [])

    def fake_eval(action, severity, agent_mode, namespace):
        # Honour the mode gate even in the mock, so observe-only tests still
        # exercise the deny path realistically.
        if agent_mode != "active":
            return PolicyDecision(
                allow=False,
                reasons=[f'agent_mode is "{agent_mode}" (must be "active" to execute)'],
            )
        return state["decision"]

    import agent.graph as g

    monkeypatch.setattr(g, "opa_evaluate", fake_eval)
    return set_decision


@pytest.fixture
def tmp_audit(monkeypatch, tmp_path):
    """Redirect audit writes to a tmp file so tests don't pollute agent/audit.log.

    The Settings dataclass is frozen, so we can't mutate
    settings.audit_log_path. Instead we install a fresh AuditLogger pointed at
    the tmp file as the module-level `audit` in agent.graph.
    """
    p = tmp_path / "audit.log"
    import agent.graph as g
    from agent.audit import AuditLogger

    monkeypatch.setattr(g, "audit", AuditLogger(path=str(p)))
    return p
