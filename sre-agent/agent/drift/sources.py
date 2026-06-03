"""Source-of-truth providers for the drift detector.

`SourceOfTruth` is the Protocol. v1 ships `ManifestsSource` — a
filesystem provider that walks a directory of YAML files. This is
the simplest case and covers the laptop demo (point at `manifests/`)
plus the most common production pattern (a Git checkout of the
cluster's declarative state mounted into the agent pod).

Future providers — ArgoCD `Application` CRs, Flux `Kustomization`
CRs, raw `git clone` of an upstream repo — implement the same
Protocol and slot in without touching the detector or comparator.

Each provider yields `TargetManifest` records: one per K8s object
the operator considers the source of truth. Multi-document YAML
files are supported (split on `---`); each document becomes its
own target. Documents that don't have a `kind` + `metadata.name`
are silently skipped.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TargetManifest:
    """One K8s object the SoT considers authoritative.

    `source` is a free-form pointer the operator can use to find
    where the manifest lives (e.g. `manifests/demo-workloads.yaml#1`
    for the second document in the file). It's surfaced in the
    drift event payload so an operator can `git blame` quickly.
    """

    kind: str
    namespace: str | None  # None for cluster-scoped kinds (Namespace, Node, …)
    name: str
    body: dict[str, Any]
    source: str
    # Per-manifest ignore-path override read from the body's
    # `metadata.annotations["pocteo.io/drift-ignore-fields"]`.
    # Comma-separated dotted paths; the comparator merges this with
    # the global whitelist before diffing.
    ignore_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def key(self) -> tuple[str, str, str]:
        """Stable lookup key for matching against live resources."""
        return (self.kind, self.namespace or "", self.name)


class SourceOfTruth(Protocol):
    """Provider protocol. Implementations yield `TargetManifest`
    records on demand. `name` is a human-readable label for logs
    and the drift-event payload."""

    name: str

    def manifests(self) -> Iterator[TargetManifest]:
        """Walk the source and yield every manifest the operator
        considers authoritative. Soft-fail per manifest — a bad
        YAML document doesn't abort the iterator."""
        ...


# Annotation operators set on the LIVE resource to tell the
# detector "ignore this path even though it's not in the
# default whitelist". Comma-separated dotted paths.
DRIFT_IGNORE_ANNOTATION = "pocteo.io/drift-ignore-fields"

# Kinds we never compare. ConfigMaps + Secrets churn frequently
# under controller writes; tracking their drift via this detector
# tends to produce noise. Operators who DO want them tracked can
# remove this filter via DRIFT_IGNORE_KINDS=ConfigMap,Secret →
# the env var subtracts from this set, not adds.
DEFAULT_SKIPPED_KINDS: frozenset[str] = frozenset(
    {
        "Event",
        "Lease",
        "Pod",  # pods are managed by their owner Deployments / StatefulSets / ...
    }
)


@dataclass
class ManifestsSource:
    """Reads YAML from a directory tree. Default v1 provider.

    Walks `root` recursively for files matching `*.yaml` / `*.yml`.
    Each multi-document file is split on `---`; each document with
    a `kind` + `metadata.name` becomes one `TargetManifest`. Files
    that don't parse are logged once at WARNING and skipped.

    `skipped_kinds` defaults to `DEFAULT_SKIPPED_KINDS`. Override
    only when you specifically want to track Pod / Event drift,
    which is rare and noisy.
    """

    root: Path
    name: str = "manifests"
    skipped_kinds: frozenset[str] = DEFAULT_SKIPPED_KINDS

    def manifests(self) -> Iterator[TargetManifest]:
        if not self.root.exists():
            log.warning("drift: manifests root %s does not exist", self.root)
            return
        for path in sorted(self.root.rglob("*.y*ml")):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("drift: cannot read %s (%s); skipping", path, exc)
                continue
            try:
                docs = list(yaml.safe_load_all(text))
            except yaml.YAMLError as exc:
                log.warning("drift: cannot parse %s (%s); skipping", path, exc)
                continue
            for i, doc in enumerate(docs):
                if not isinstance(doc, dict):
                    continue
                kind = doc.get("kind")
                meta = doc.get("metadata") or {}
                name = meta.get("name") if isinstance(meta, dict) else None
                if not isinstance(kind, str) or not isinstance(name, str):
                    continue
                if kind in self.skipped_kinds:
                    continue
                namespace = None
                if isinstance(meta, dict) and isinstance(meta.get("namespace"), str):
                    namespace = meta["namespace"]
                # Per-manifest ignore-paths override.
                ignore_paths: tuple[str, ...] = ()
                annotations = meta.get("annotations") if isinstance(meta, dict) else None
                if isinstance(annotations, dict):
                    raw = annotations.get(DRIFT_IGNORE_ANNOTATION)
                    if isinstance(raw, str) and raw.strip():
                        ignore_paths = tuple(p.strip() for p in raw.split(",") if p.strip())
                yield TargetManifest(
                    kind=kind,
                    namespace=namespace,
                    name=name,
                    body=doc,
                    source=f"{path}#{i}" if len(docs) > 1 else str(path),
                    ignore_paths=ignore_paths,
                )


def source_from_env() -> SourceOfTruth | None:
    """Build the configured SoT provider from env. Returns None when
    drift detection is explicitly disabled.

    Knobs:
      - DRIFT_SOURCE_TYPE=manifests | none (default: manifests)
      - DRIFT_SOURCE_PATH=path/to/manifests (default: ./manifests)

    Future provider types (`argocd`, `flux`, `git`) plug in here
    without touching the detector or anything else.
    """
    kind = os.environ.get("DRIFT_SOURCE_TYPE", "manifests").strip().lower()
    if kind in ("", "none", "off", "disabled"):
        return None
    if kind == "manifests":
        root_str = os.environ.get("DRIFT_SOURCE_PATH", "./manifests")
        return ManifestsSource(root=Path(root_str).resolve())
    log.warning("drift: unknown DRIFT_SOURCE_TYPE=%r; supported: manifests, none", kind)
    return None
