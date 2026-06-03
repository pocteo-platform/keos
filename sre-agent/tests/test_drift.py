"""Hermetic tests for the Phase 15 drift package.

No live K8s, no live NATS. We drive the four moving pieces:

  * `comparator.compare_resources` — pure-Python deep diff with the
    server-field whitelist.
  * `sources.ManifestsSource` — filesystem provider.
  * `detector.DriftDetector.tick_once` — end-to-end with a fake
    `_LiveLookup` and a fake `NATSPublisher`.
  * `tools.revert_drift` — dry-run + sot_source parsing + the
    defensive guard against mismatched manifests.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from agent.drift.comparator import (
    DEFAULT_WHITELIST_PATHS,
    compare_resources,
    strip_server_fields,
)
from agent.drift.detector import DRIFT_EVENT_PRIORITY, DriftDetector
from agent.drift.sources import (
    DRIFT_IGNORE_ANNOTATION,
    ManifestsSource,
    TargetManifest,
)
from agent.tools import revert_drift

# ─── comparator: strip_server_fields ─────────────────────────────────────


def test_strip_drops_server_metadata() -> None:
    body = {
        "kind": "Deployment",
        "metadata": {
            "name": "x",
            "uid": "abc-123",
            "resourceVersion": "42",
            "generation": 7,
            "creationTimestamp": "2026-06-03T00:00:00Z",
            "managedFields": [{"manager": "kubectl"}],
        },
        "spec": {"replicas": 3},
        "status": {"readyReplicas": 3},
    }
    out = strip_server_fields(body)
    assert "uid" not in out["metadata"]
    assert "resourceVersion" not in out["metadata"]
    assert "managedFields" not in out["metadata"]
    assert "status" not in out


def test_strip_drops_hpa_owned_replicas() -> None:
    """Phase 15 — spec.replicas is whitelisted unconditionally so HPA
    drift doesn't produce false positives. Operators who want to
    track it explicitly pin via the per-manifest ignore-annotation
    inverse (omit the override → use the global whitelist)."""
    body = {"kind": "Deployment", "metadata": {"name": "x"}, "spec": {"replicas": 5}}
    out = strip_server_fields(body)
    assert "replicas" not in out["spec"]


def test_strip_drops_last_applied_annotation() -> None:
    body = {
        "kind": "ConfigMap",
        "metadata": {
            "name": "x",
            "annotations": {
                "kubectl.kubernetes.io/last-applied-configuration": "{...}",
                "my.app/label": "keep-me",
            },
        },
    }
    out = strip_server_fields(body)
    ann = out["metadata"]["annotations"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in ann
    assert ann["my.app/label"] == "keep-me"


def test_strip_preserves_user_spec() -> None:
    body = {
        "kind": "Deployment",
        "metadata": {"name": "x"},
        "spec": {"template": {"spec": {"containers": [{"name": "c", "image": "x:1"}]}}},
    }
    out = strip_server_fields(body)
    assert out["spec"]["template"]["spec"]["containers"][0]["image"] == "x:1"


def test_strip_handles_extra_paths() -> None:
    body = {"metadata": {"name": "x"}, "spec": {"foo": 1, "bar": 2}}
    out = strip_server_fields(body, extra_paths=("spec.foo",))
    assert "foo" not in out["spec"]
    assert out["spec"]["bar"] == 2


def test_strip_wildcard_traverses_list() -> None:
    """The `spec.ports.*.nodePort` whitelist entry uses the `*` wildcard."""
    body = {
        "kind": "Service",
        "metadata": {"name": "x"},
        "spec": {
            "ports": [
                {"port": 80, "nodePort": 30001},
                {"port": 443, "nodePort": 30002},
            ]
        },
    }
    out = strip_server_fields(body)
    assert "nodePort" not in out["spec"]["ports"][0]
    assert "nodePort" not in out["spec"]["ports"][1]
    # Other port fields preserved.
    assert out["spec"]["ports"][0]["port"] == 80


def test_default_whitelist_covers_critical_paths() -> None:
    """Regression guard: the whitelist must always carry the five
    highest-noise paths. Removing any one of these would flood the
    operator with false-positive drift."""
    must_include: set[tuple[str, ...]] = {
        ("metadata", "resourceVersion"),
        ("metadata", "managedFields"),
        ("status",),
        ("spec", "replicas"),
        ("metadata", "annotations", "kubectl.kubernetes.io/last-applied-configuration"),
    }
    assert must_include <= set(DEFAULT_WHITELIST_PATHS)


# ─── comparator: compare_resources ───────────────────────────────────────


def _deployment(name: str, image: str = "nginx:1.27", replicas: int = 1) -> dict:
    return {
        "kind": "Deployment",
        "apiVersion": "apps/v1",
        "metadata": {"name": name, "namespace": "workloads"},
        "spec": {
            "replicas": replicas,
            "template": {
                "spec": {
                    "containers": [{"name": "c", "image": image}],
                }
            },
        },
    }


def test_compare_returns_no_deltas_when_in_sync() -> None:
    sot = _deployment("nginx-demo", image="nginx:1.27")
    live = _deployment("nginx-demo", image="nginx:1.27")
    report = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="nginx-demo", source="m.yaml"
    )
    assert not report.drifted
    assert report.deltas == []


def test_compare_ignores_replica_drift_by_default() -> None:
    """HPA legitimately scales replicas. The default whitelist
    suppresses the drift event so the operator only sees
    operator-caused divergences."""
    sot = _deployment("x", replicas=3)
    live = _deployment("x", replicas=8)
    report = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="x", source="m.yaml"
    )
    assert not report.drifted


def test_compare_catches_image_drift() -> None:
    sot = _deployment("x", image="nginx:1.27")
    live = _deployment("x", image="nginx:1.99-malicious")
    report = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="x", source="m.yaml"
    )
    assert report.drifted
    assert any(d.path.endswith(".image") for d in report.deltas)
    delta = next(d for d in report.deltas if d.path.endswith(".image"))
    assert delta.kind == "modified"
    assert delta.sot == "nginx:1.27"
    assert delta.live == "nginx:1.99-malicious"


def test_compare_catches_added_container_env() -> None:
    sot = _deployment("x")
    live = _deployment("x")
    live["spec"]["template"]["spec"]["containers"][0]["env"] = [{"name": "DEBUG", "value": "1"}]
    report = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="x", source="m.yaml"
    )
    assert report.drifted
    assert any("env" in d.path for d in report.deltas)


def test_compare_extra_ignore_paths_override() -> None:
    """A per-manifest annotation can suppress drift on a path the
    operator considers controller-owned even when the global
    whitelist doesn't cover it."""
    sot = _deployment("x")
    live = _deployment("x")
    live["spec"]["paused"] = True
    # Without the override, we'd see drift.
    base = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="x", source="m.yaml"
    )
    assert base.drifted
    # With the override, no drift.
    suppressed = compare_resources(
        sot,
        live,
        kind="Deployment",
        namespace="workloads",
        name="x",
        source="m.yaml",
        extra_ignore_paths=("spec.paused",),
    )
    assert not suppressed.drifted


def test_compare_reports_added_and_removed_keys() -> None:
    sot = _deployment("x")
    sot["spec"]["template"]["spec"]["containers"][0]["resources"] = {"limits": {"memory": "256Mi"}}
    live = _deployment("x")
    # live has no resources field.
    report = compare_resources(
        sot, live, kind="Deployment", namespace="workloads", name="x", source="m.yaml"
    )
    assert report.drifted
    removed = next(d for d in report.deltas if "resources" in d.path)
    assert removed.kind == "removed"


# ─── sources: ManifestsSource ────────────────────────────────────────────


def test_manifests_source_yields_each_doc(tmp_path: Path) -> None:
    yaml_text = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: a\n"
        "  namespace: workloads\n"
        "spec: {}\n"
        "---\n"
        "apiVersion: v1\n"
        "kind: Service\n"
        "metadata:\n"
        "  name: b\n"
        "  namespace: workloads\n"
        "spec: {}\n"
    )
    f = tmp_path / "both.yaml"
    f.write_text(yaml_text, encoding="utf-8")
    src = ManifestsSource(root=tmp_path)
    out = list(src.manifests())
    assert {t.kind for t in out} == {"Deployment", "Service"}
    assert all(t.namespace == "workloads" for t in out)
    # Multi-doc files carry a `#index` suffix on `source`.
    assert all("#" in t.source for t in out)


def test_manifests_source_skips_pods_by_default(tmp_path: Path) -> None:
    """Pods are managed by their owner; tracking their drift via
    the detector tends to produce noise. Default ManifestsSource
    skips them. Operator overrides by passing a different
    skipped_kinds."""
    text = "apiVersion: v1\nkind: Pod\nmetadata:\n  name: a\nspec: {}\n"
    (tmp_path / "p.yaml").write_text(text, encoding="utf-8")
    assert list(ManifestsSource(root=tmp_path).manifests()) == []


def test_manifests_source_picks_up_ignore_annotation(tmp_path: Path) -> None:
    text = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: a\n"
        "  namespace: workloads\n"
        "  annotations:\n"
        f"    {DRIFT_IGNORE_ANNOTATION}: spec.replicas, spec.paused\n"
        "spec: {}\n"
    )
    (tmp_path / "d.yaml").write_text(text, encoding="utf-8")
    [t] = list(ManifestsSource(root=tmp_path).manifests())
    assert t.ignore_paths == ("spec.replicas", "spec.paused")


def test_manifests_source_skips_unparseable_files(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("not: a [valid yaml\n", encoding="utf-8")
    (tmp_path / "good.yaml").write_text(
        "apiVersion: v1\nkind: ConfigMap\nmetadata: {name: x}\ndata: {}\n",
        encoding="utf-8",
    )
    out = list(ManifestsSource(root=tmp_path).manifests())
    assert {t.name for t in out} == {"x"}


def test_manifests_source_missing_root_returns_empty(tmp_path: Path) -> None:
    out = list(ManifestsSource(root=tmp_path / "nope").manifests())
    assert out == []


# ─── detector: tick_once with fakes ──────────────────────────────────────


class FakeLookup:
    """Stand-in for `_LiveLookup` that returns canned data per
    (kind, ns, name)."""

    def __init__(self, table: dict[tuple[str, str | None, str], dict | None]) -> None:
        self.table = table

    def fetch(self, kind: str, namespace: str | None, name: str) -> dict | None:
        return self.table.get((kind, namespace, name))


class FakePublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def publish(self, subject: str, payload: dict) -> bool:
        self.calls.append((subject, payload))
        return True

    async def close(self) -> None:
        return None


class _StaticSource:
    """Minimal in-memory SoT. Single iteration; no fs access."""

    name = "static-test"

    def __init__(self, targets: list[TargetManifest]) -> None:
        self.targets = targets

    def manifests(self):
        yield from self.targets


def _target(kind: str, name: str, image: str = "nginx:1.27") -> TargetManifest:
    body = _deployment(name, image=image)
    return TargetManifest(
        kind=kind,
        namespace="workloads",
        name=name,
        body=body,
        source=f"fake://{kind}/{name}",
    )


@pytest.mark.asyncio
async def test_detector_publishes_on_drift() -> None:
    sot_target = _target("Deployment", "nginx-demo", image="nginx:1.27")
    live = _deployment("nginx-demo", image="nginx:1.99-bad")
    pub = FakePublisher()
    det = DriftDetector(
        source=_StaticSource([sot_target]),
        lookup=FakeLookup({("Deployment", "workloads", "nginx-demo"): live}),
        publisher=pub,
        tick_interval_s=30,
    )
    n = await det.tick_once()
    assert n == 1
    subject, payload = pub.calls[0]
    assert subject == "infra.events.drift.Deployment"
    assert payload["rule"] == "GitOps drift on Deployment"
    assert payload["priority"] == DRIFT_EVENT_PRIORITY["Deployment"]
    fields = payload["output_fields"]
    assert fields["k8s.kind"] == "Deployment"
    assert fields["k8s.name"] == "nginx-demo"
    assert fields["drift.source"] == "fake://Deployment/nginx-demo"
    assert any("image" in p for p in fields["drift.paths"])


@pytest.mark.asyncio
async def test_detector_does_not_publish_when_in_sync() -> None:
    sot_target = _target("Deployment", "nginx-demo")
    pub = FakePublisher()
    det = DriftDetector(
        source=_StaticSource([sot_target]),
        lookup=FakeLookup({("Deployment", "workloads", "nginx-demo"): _deployment("nginx-demo")}),
        publisher=pub,
        tick_interval_s=30,
    )
    assert await det.tick_once() == 0
    assert pub.calls == []


@pytest.mark.asyncio
async def test_detector_skips_missing_live_resource() -> None:
    """Live fetch returning None should NOT produce a drift event in
    v1 — that's the operator's "deploy this manifest" workflow, not
    drift. A future phase may add an explicit `missing` event kind."""
    pub = FakePublisher()
    det = DriftDetector(
        source=_StaticSource([_target("Deployment", "ghost")]),
        lookup=FakeLookup({("Deployment", "workloads", "ghost"): None}),
        publisher=pub,
        tick_interval_s=30,
    )
    assert await det.tick_once() == 0


@pytest.mark.asyncio
async def test_detector_dedups_within_cooldown() -> None:
    sot_target = _target("Deployment", "x", image="nginx:1.27")
    live = _deployment("x", image="nginx:1.99")
    pub = FakePublisher()
    det = DriftDetector(
        source=_StaticSource([sot_target]),
        lookup=FakeLookup({("Deployment", "workloads", "x"): live}),
        publisher=pub,
        tick_interval_s=30,
        dedup_window_s=300,
    )
    n1 = await det.tick_once()
    n2 = await det.tick_once()
    assert n1 == 1
    assert n2 == 0
    # Force expire and retick — should publish again.
    for k in list(det._cooldowns.keys()):
        det._cooldowns[k] = 0.0
    assert await det.tick_once() == 1


@pytest.mark.asyncio
async def test_detector_swallows_per_target_errors() -> None:
    """If `_maybe_event` raises on one target, the next still gets
    processed. Mirrors the forecast scheduler's guard."""
    good = _target("Deployment", "good", image="nginx:1.27")
    bad = _target("Deployment", "bad", image="nginx:1.27")
    pub = FakePublisher()
    det = DriftDetector(
        source=_StaticSource([bad, good]),
        lookup=FakeLookup(
            {
                ("Deployment", "workloads", "good"): _deployment("good", image="nginx:1.99"),
                ("Deployment", "workloads", "bad"): _deployment("bad", image="nginx:1.99"),
            }
        ),
        publisher=pub,
        tick_interval_s=30,
    )
    real = det._maybe_event

    def _raising(tgt, now):
        if tgt.name == "bad":
            raise RuntimeError("synthetic")
        return real(tgt, now)

    det._maybe_event = _raising  # type: ignore[assignment]
    published = await det.tick_once()
    assert published == 1
    assert pub.calls[0][1]["output_fields"]["k8s.name"] == "good"


# ─── tools.revert_drift ──────────────────────────────────────────────────


def test_revert_drift_rejects_empty_sot_source() -> None:
    result = revert_drift(kind="Deployment", namespace="workloads", name="x", sot_source="")
    assert not result.success
    assert "sot_source" in (result.error or "")


def test_revert_drift_dry_run_with_single_doc() -> None:
    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="x",
        sot_source="/tmp/manifest.yaml",
        dry_run=True,
    )
    assert result.success
    assert result.dry_run
    assert "kubectl apply -f /tmp/manifest.yaml" in result.stdout


def test_revert_drift_parses_doc_index_in_source() -> None:
    """`sot_source` with `#N` should round-trip to (path, doc_index)."""
    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="x",
        sot_source="/tmp/manifest.yaml#2",
        dry_run=True,
    )
    assert result.success
    assert result.dry_run
    assert "doc 2" in result.stdout


def test_revert_drift_rejects_non_integer_doc_index() -> None:
    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="x",
        sot_source="/tmp/manifest.yaml#not-a-number",
    )
    assert not result.success
    assert "non-integer" in (result.error or "")


def test_revert_drift_reports_missing_file(tmp_path: Path) -> None:
    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="x",
        sot_source=str(tmp_path / "missing.yaml"),
        dry_run=False,
    )
    assert not result.success
    assert "not found" in (result.error or "")


def test_revert_drift_refuses_kind_name_mismatch(tmp_path: Path) -> None:
    """If the SoT YAML has been swapped on disk, the live tool should
    refuse to apply rather than blast the wrong resource."""
    # Write a manifest whose kind/name don't match the request.
    text = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: other-thing\n"
        "  namespace: workloads\n"
        "data: {}\n"
    )
    path = tmp_path / "c.yaml"
    path.write_text(text, encoding="utf-8")
    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="my-deploy",
        sot_source=str(path),
        dry_run=False,
    )
    assert not result.success
    assert "does not match" in (result.error or "")


# ─── revert_drift happy path (apply succeeds) ────────────────────────────


def test_revert_drift_happy_path_invokes_kubectl_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the SoT manifest matches the requested target, the tool
    should invoke `kubectl apply -f <tmp>` with the single-doc JSON
    payload and return success."""
    import json
    from agent import tools as tools_mod

    # Write a single-doc SoT manifest matching what we'll request.
    text = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: nginx-demo\n"
        "  namespace: workloads\n"
        "spec:\n"
        "  replicas: 3\n"
    )
    path = tmp_path / "d.yaml"
    path.write_text(text, encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_kubectl(args: list[str]) -> tuple[int, str, str]:
        # Capture the temp file path the tool wrote, parse + verify
        # only the matching doc was extracted.
        captured["args"] = args
        assert args[0] == "apply"
        assert args[1] == "-f"
        with open(args[2], encoding="utf-8") as fh:
            captured["body"] = json.load(fh)
        return (0, "deployment.apps/nginx-demo configured\n", "")

    monkeypatch.setattr(tools_mod, "_kubectl", fake_kubectl)

    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="nginx-demo",
        sot_source=str(path),
        dry_run=False,
    )
    assert result.success
    assert "configured" in result.stdout
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["kind"] == "Deployment"
    assert body["metadata"]["name"] == "nginx-demo"
    assert body["spec"]["replicas"] == 3
    # Temp file should be cleaned up after the apply.
    assert not Path(str(captured["args"][2])).exists()  # type: ignore[index]


def test_revert_drift_picks_correct_doc_from_multi_doc_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-doc YAML file → only the doc at the requested index is
    passed to kubectl, NOT the whole file. Otherwise re-applying
    would blast every drifted resource in the bundle at once."""
    import json
    from agent import tools as tools_mod

    text = (
        "apiVersion: v1\n"
        "kind: ConfigMap\n"
        "metadata:\n"
        "  name: other-cm\n"
        "  namespace: workloads\n"
        "data:\n"
        "  k: v\n"
        "---\n"
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata:\n"
        "  name: target-deploy\n"
        "  namespace: workloads\n"
        "spec:\n"
        "  replicas: 2\n"
    )
    path = tmp_path / "multi.yaml"
    path.write_text(text, encoding="utf-8")

    captured_body: dict[str, object] = {}

    def fake_kubectl(args: list[str]) -> tuple[int, str, str]:
        with open(args[2], encoding="utf-8") as fh:
            captured_body.update(json.load(fh))
        return (0, "deployment.apps/target-deploy configured\n", "")

    monkeypatch.setattr(tools_mod, "_kubectl", fake_kubectl)

    result = revert_drift(
        kind="Deployment",
        namespace="workloads",
        name="target-deploy",
        sot_source=f"{path}#1",
        dry_run=False,
    )
    assert result.success
    assert captured_body["kind"] == "Deployment"
    assert captured_body["metadata"]["name"] == "target-deploy"  # type: ignore[index]


def test_revert_drift_surfaces_kubectl_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When kubectl returns non-zero, the tool must report success=False
    and propagate stderr — operator UI shows this in the action result."""
    from agent import tools as tools_mod

    text = (
        "apiVersion: apps/v1\n"
        "kind: Deployment\n"
        "metadata: { name: x, namespace: workloads }\n"
        "spec: { replicas: 1 }\n"
    )
    path = tmp_path / "x.yaml"
    path.write_text(text, encoding="utf-8")

    def fake_kubectl(_args: list[str]) -> tuple[int, str, str]:
        return (1, "", "error: namespaces 'workloads' not found")

    monkeypatch.setattr(tools_mod, "_kubectl", fake_kubectl)
    result = revert_drift(
        kind="Deployment", namespace="workloads", name="x", sot_source=str(path)
    )
    assert not result.success
    assert "kubectl exit 1" in (result.error or "")
    assert "namespaces 'workloads' not found" in (result.stderr or "")


def test_revert_drift_handles_unparseable_yaml(
    tmp_path: Path,
) -> None:
    """A corrupted SoT file shouldn't crash the tool; it should return
    a clear error so the agent can escalate."""
    path = tmp_path / "bad.yaml"
    path.write_text("not: : : valid: yaml: :", encoding="utf-8")
    result = revert_drift(
        kind="Deployment", namespace="workloads", name="x", sot_source=str(path)
    )
    assert not result.success
    assert "cannot parse" in (result.error or "")


# ─── Severity priority map ───────────────────────────────────────────────


def test_drift_priority_high_for_workload_kinds() -> None:
    """Operator UI groups drift events by severity; the most-
    critical kinds (Deployment, StatefulSet, DaemonSet) MUST fire
    at high so they bubble to the top of /reliability."""
    assert DRIFT_EVENT_PRIORITY["Deployment"] == "high"
    assert DRIFT_EVENT_PRIORITY["StatefulSet"] == "high"
    assert DRIFT_EVENT_PRIORITY["DaemonSet"] == "high"


def test_drift_priority_default_when_unknown_kind() -> None:
    assert DRIFT_EVENT_PRIORITY["_default"] == "medium"
    # A made-up kind falls through to the default in the detector.
    # We assert the constant exists; runtime fallback uses .get().


# ─── time round-trip helper ──────────────────────────────────────────────


def test_now_is_recent_enough_for_test_keys() -> None:
    """Sanity: time.time() works and is sane in the test environment.
    The detector relies on this for cooldown bookkeeping."""
    now = time.time()
    assert now > 1_700_000_000  # year 2023 unix seconds, generous lower bound
