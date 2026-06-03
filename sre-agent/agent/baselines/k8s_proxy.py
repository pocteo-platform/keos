"""Prometheus access via the Kubernetes apiserver proxy.

The agent runs in docker-compose (or as a pod outside the cluster
in some edge installs). The in-cluster Prometheus (deployed by
`manifests/monitoring.yaml`) exposes a ClusterIP service — not
reachable directly from compose. Rather than punch a NodePort or
LoadBalancer through, we proxy every PromQL call through the
existing Kubernetes apiserver, reusing the kubeconfig the agent
already has for `mode_store` and `chaos_observer`.

The apiserver proxy URL pattern:

    GET /api/v1/namespaces/<ns>/services/<svc>:<port>/proxy/api/v1/query?query=...

The kubernetes-client SDK's high-level `connect_get_namespaced_service_proxy_with_path`
helper URL-encodes the entire `path` argument, which breaks the `?`
separating the path from the query string. We bypass it and call
`api_client.call_api()` directly so `query_params` lands on the
URL as a proper querystring.

We use the call synchronously inside a thread (the learner is async;
we wrap the sync call with `asyncio.to_thread`).

Configuration is by URL scheme. The learner picks this client when
`PROMETHEUS_URL` starts with `k8s-proxy://`:

    k8s-proxy://monitoring/prometheus:9090

Parsed as `namespace=monitoring`, `service=prometheus`, `port=9090`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProxyTarget:
    """Parsed `k8s-proxy://<ns>/<svc>:<port>` URL."""

    namespace: str
    service: str
    port: int

    @classmethod
    def from_url(cls, url: str) -> ProxyTarget:
        """Parse `k8s-proxy://<ns>/<svc>[:<port>]` into a ProxyTarget.

        Defaults port to 9090 (the conventional Prometheus port) when
        unspecified so a one-line override like
        `PROMETHEUS_URL=k8s-proxy://monitoring/prometheus` works.
        """
        if not url.startswith("k8s-proxy://"):
            raise ValueError(f"not a k8s-proxy URL: {url!r}")
        body = url[len("k8s-proxy://") :].strip("/")
        if "/" not in body:
            raise ValueError(f"k8s-proxy URL missing namespace/service: {url!r}")
        ns, rest = body.split("/", 1)
        if ":" in rest:
            svc, port_s = rest.split(":", 1)
            try:
                port = int(port_s)
            except ValueError as exc:
                raise ValueError(f"invalid port in {url!r}") from exc
        else:
            svc = rest
            port = 9090
        if not ns or not svc:
            raise ValueError(f"k8s-proxy URL has empty ns or svc: {url!r}")
        return cls(namespace=ns, service=svc, port=port)


class K8sProxyClient:
    """Synchronous helper that issues PromQL `/api/v1/query` calls
    through the Kubernetes apiserver proxy.

    Holds its own `kubernetes.client.CoreV1Api` instance configured
    from `KUBECONFIG` (the same path the rest of the agent uses).
    Lazy-loads the client so import-time failures (kubeconfig
    missing in tests) don't crash the module.
    """

    def __init__(self, target: ProxyTarget) -> None:
        self.target = target
        self._core_v1: object | None = None

    def _client(self) -> object:
        if self._core_v1 is not None:
            return self._core_v1
        # Defer the import so the rest of the package stays importable
        # in environments without kubernetes-client installed (e.g.
        # the hermetic unit-test image).
        from kubernetes import client, config  # type: ignore[import-not-found]

        try:
            config.load_kube_config()
        except Exception:
            # Fall back to in-cluster (the agent might be deployed as
            # a pod via the Helm chart).
            config.load_incluster_config()
        self._core_v1 = client.CoreV1Api()
        return self._core_v1

    def query(self, promql: str) -> dict:
        """Synchronous PromQL `/api/v1/query`. Raises on transport
        errors so the caller (`Learner.tick_once`) can route them
        through its existing wide try/except + WARNING log.

        Bypasses the high-level `connect_get_namespaced_service_proxy_with_path`
        SDK call because it URL-encodes the entire path (including
        the `?` separating the PromQL endpoint from the querystring),
        producing a 404. Calling `api_client.call_api()` directly
        lets us send `query_params` as a proper URL querystring.
        """
        core_v1 = self._client()
        api_client = core_v1.api_client  # type: ignore[attr-defined]
        name = f"{self.target.service}:{self.target.port}"
        # `_preload_content=False` returns the raw urllib3.HTTPResponse;
        # `.data` is the unmodified bytes Prometheus actually sent. The
        # default `_preload_content=True` runs `str(deserialized_dict)`
        # which turns the JSON into Python-repr (single quotes) and
        # makes json.loads fail downstream.
        resp = api_client.call_api(
            "/api/v1/namespaces/{namespace}/services/{name}/proxy/{path}",
            "GET",
            path_params={
                "namespace": self.target.namespace,
                "name": name,
                "path": "api/v1/query",
            },
            query_params={"query": promql},
            auth_settings=["BearerToken"],
            _preload_content=False,
            _return_http_data_only=True,
        )
        raw = resp.data if hasattr(resp, "data") else resp
        body = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"apiserver proxy returned non-JSON for {promql!r}: {body[:200]}"
            ) from exc

    async def query_async(self, promql: str) -> dict:
        """Async wrapper — runs `query` in a thread so the learner's
        asyncio loop doesn't block on the synchronous kubernetes
        client. Same exception semantics."""
        return await asyncio.to_thread(self.query, promql)
