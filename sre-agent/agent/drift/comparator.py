"""Drift comparator — pure-Python deep diff with field whitelist.

The comparator is the bug-prone heart of drift detection. Two
non-obvious requirements drive its design:

* **Server-controlled fields drift constantly.** `metadata.resourceVersion`,
  `metadata.generation`, `status.*`, `metadata.managedFields`, etc.
  are written by the K8s API server on every reconcile. If we
  reported those as drift, we'd flood the operator with
  meaningless events.

* **Other controllers legitimately mutate spec fields.** HPA
  writes `spec.replicas`. Kubectl rollout writes
  `spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"]`.
  Treating these as drift causes false positives the operator
  can't fix without disabling the controller.

The whitelist below is conservative — anything that's likely to
be controller-owned is stripped. Operators with a niche custom
controller can extend it via the `pocteo.io/drift-ignore-fields`
annotation on the manifest in question.

The diff itself is a small recursive descent over the (stripped)
SoT and live trees. Reports a list of `(path, sot_value, live_value)`
tuples — the operator-facing audit shape.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# Default paths the comparator strips before diffing. Each is a
# **tuple of path components** rather than a dotted string — K8s
# annotation keys legitimately contain `.` (e.g.
# `kubectl.kubernetes.io/last-applied-configuration`), so a `.`-
# split would mis-parse them. `*` matches one path component
# (use for list-of-objects traversal like ports).
DEFAULT_WHITELIST_PATHS: tuple[tuple[str, ...], ...] = (
    # ---- Server-controlled metadata ----
    ("metadata", "uid"),
    ("metadata", "resourceVersion"),
    ("metadata", "generation"),
    ("metadata", "creationTimestamp"),
    ("metadata", "deletionTimestamp"),
    ("metadata", "deletionGracePeriodSeconds"),
    ("metadata", "selfLink"),
    ("metadata", "managedFields"),
    ("metadata", "finalizers"),
    # ---- Annotation noise written by kubectl / Helm / Argo ----
    ("metadata", "annotations", "kubectl.kubernetes.io/last-applied-configuration"),
    ("metadata", "annotations", "deployment.kubernetes.io/revision"),
    ("metadata", "annotations", "control-plane.alpha.kubernetes.io/leader"),
    ("metadata", "annotations", "meta.helm.sh/release-name"),
    ("metadata", "annotations", "meta.helm.sh/release-namespace"),
    ("metadata", "annotations", "argocd.argoproj.io/tracking-id"),
    ("metadata", "annotations", "argocd.argoproj.io/sync-wave"),
    ("metadata", "annotations", "kubernetes.io/config.hash"),
    # ---- Status is server-owned end-to-end ----
    ("status",),
    # ---- HPA-managed fields on Deployment / StatefulSet / RC ----
    # When the workload also has a HorizontalPodAutoscaler the
    # detector strips this before diffing. We strip unconditionally
    # for simplicity — operators who pin `spec.replicas` in SoT
    # and want drift on that override via the per-manifest ignore
    # annotation.
    ("spec", "replicas"),
    # ---- Service-clusterIP is server-assigned ----
    ("spec", "clusterIP"),
    ("spec", "clusterIPs"),
    ("spec", "ipFamilies"),
    ("spec", "ipFamilyPolicy"),
    # ---- Service node-port assignment, when unset in SoT ----
    ("spec", "ports", "*", "nodePort"),
    # ---- ServiceAccount auto-mounted token ----
    ("secrets",),
)


def _parse_user_path(path: str) -> tuple[str, ...]:
    """Parse a user-supplied dotted path into a tuple of components.

    The per-manifest `pocteo.io/drift-ignore-fields` annotation
    accepts dotted strings (operator-friendly). Annotation keys
    aren't usable inside this user-input syntax because of the `.`
    ambiguity; operators targeting annotation paths must use the
    underlying tuple form via a custom whitelist override at
    deploy time. The runbook documents this.
    """
    return tuple(path.split("."))


@dataclass(frozen=True)
class DriftDelta:
    """One unauthorised divergence the comparator found.

    `path` is the dotted location inside the resource body.
    `sot` is what the source of truth says it should be;
    `live` is what the live cluster has. `kind` distinguishes
    add (SoT has it, live doesn't), remove (live has it,
    SoT doesn't), and modify.
    """

    path: str
    kind: str  # "added" | "removed" | "modified"
    sot: Any
    live: Any


@dataclass
class DriftReport:
    """Output of one comparator pass for one (kind, ns, name)
    target. Empty `deltas` means in-sync."""

    kind: str
    namespace: str | None
    name: str
    deltas: list[DriftDelta]
    source: str  # the SoT manifest's `source` (file path #doc)

    @property
    def drifted(self) -> bool:
        return len(self.deltas) > 0


# ---------------------------------------------------------------------------
# Field-stripping pass.
# ---------------------------------------------------------------------------


def _strip_path(node: Any, parts: tuple[str, ...] | list[str]) -> None:
    """Mutates `node` in place to drop the field at the given path."""
    if not parts or not isinstance(node, dict | list):
        return
    head, rest = parts[0], parts[1:]
    if head == "*":
        # Wildcard one-level: apply to every child.
        if isinstance(node, list):
            for item in node:
                _strip_path(item, rest)
        elif isinstance(node, dict):
            for v in node.values():
                _strip_path(v, rest)
        return
    if isinstance(node, list):
        # Lists don't have keyed paths in our whitelist (we always
        # use `*` for list traversal); a bare key here means the
        # whitelist path doesn't apply.
        return
    if head not in node:
        return
    if not rest:
        del node[head]
        return
    _strip_path(node[head], rest)


def strip_server_fields(body: dict[str, Any], extra_paths: Iterable[str] = ()) -> dict[str, Any]:
    """Return a deep copy of `body` with every whitelist path removed.

    The output is the comparator's actual input — both SoT and live
    bodies are stripped the same way before the diff. `extra_paths`
    lets a per-manifest annotation extend the global whitelist
    without rewriting it.
    """
    import copy

    out = copy.deepcopy(body)
    for path in DEFAULT_WHITELIST_PATHS:
        _strip_path(out, path)
    for path in extra_paths:
        _strip_path(out, _parse_user_path(path))
    return out


# ---------------------------------------------------------------------------
# Diff.
# ---------------------------------------------------------------------------


_MISSING = object()


def _diff(sot: Any, live: Any, prefix: str, out: list[DriftDelta]) -> None:
    """Recursive descent; appends to `out`. Considers an explicit
    `None` on the SoT side as "field absent" so SoT writers don't
    have to omit keys to mean "default"."""
    if sot is None and live is None:
        return
    if sot is None and live is not None:
        out.append(DriftDelta(path=prefix or "(root)", kind="added", sot=_MISSING, live=live))
        return
    if live is None and sot is not None:
        out.append(DriftDelta(path=prefix or "(root)", kind="removed", sot=sot, live=_MISSING))
        return
    if type(sot) is not type(live):
        out.append(DriftDelta(path=prefix or "(root)", kind="modified", sot=sot, live=live))
        return
    if isinstance(sot, dict):
        # Keys on either side that aren't in both → added/removed.
        all_keys = set(sot.keys()) | set(live.keys())
        for k in sorted(all_keys):
            child = f"{prefix}.{k}" if prefix else k
            if k not in live:
                out.append(DriftDelta(path=child, kind="removed", sot=sot[k], live=_MISSING))
            elif k not in sot:
                out.append(DriftDelta(path=child, kind="added", sot=_MISSING, live=live[k]))
            else:
                _diff(sot[k], live[k], child, out)
        return
    if isinstance(sot, list):
        # Lists are compared positionally. K8s does smart merge in
        # places (container ports, env vars), which we don't model
        # — a list-order difference shows up as a modification at
        # the same index. Good enough for v1; operators who hit
        # false positives on list order can use the ignore
        # annotation.
        if len(sot) != len(live):
            out.append(
                DriftDelta(
                    path=prefix or "(root)",
                    kind="modified",
                    sot=sot,
                    live=live,
                )
            )
            return
        for i, (a, b) in enumerate(zip(sot, live, strict=False)):
            _diff(a, b, f"{prefix}[{i}]", out)
        return
    # Scalars.
    if sot != live:
        out.append(DriftDelta(path=prefix or "(root)", kind="modified", sot=sot, live=live))


def compare_resources(
    sot_body: dict[str, Any],
    live_body: dict[str, Any],
    *,
    kind: str,
    namespace: str | None,
    name: str,
    source: str,
    extra_ignore_paths: Iterable[str] = (),
) -> DriftReport:
    """Strip + diff one (kind, ns, name) target. Returns a
    `DriftReport` whose `drifted` flag tells the detector whether
    to publish an event."""
    sot_stripped = strip_server_fields(sot_body, extra_ignore_paths)
    live_stripped = strip_server_fields(live_body, extra_ignore_paths)
    deltas: list[DriftDelta] = []
    _diff(sot_stripped, live_stripped, "", deltas)
    return DriftReport(
        kind=kind,
        namespace=namespace,
        name=name,
        deltas=deltas,
        source=source,
    )
