"""Centralised configuration loaded from .env / environment variables.

Every module imports `settings` from here instead of touching os.environ directly,
so tests can monkey-patch a single object.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env at import time so CLI entrypoints don't have to remember.
# `.env` is searched relative to the current working directory; falls back to
# the repo root if the caller is inside `agent/`.
_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_REPO_ROOT / ".env")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


# Not frozen: tests need to monkeypatch settings.audit_log_path et al. Production
# code never mutates this object; relying on convention rather than the type.
@dataclass(frozen=False)
class Settings:
    ollama_host: str = _env("OLLAMA_HOST", "http://localhost:11434")
    qdrant_url: str = _env("QDRANT_URL", "http://localhost:6333")
    nats_url: str = _env("NATS_URL", "nats://localhost:4222")
    opa_url: str = _env("OPA_URL", "http://localhost:8181")

    # qwen2.5:3b-instruct-q4_K_M is the default everywhere (compose, Helm,
    # installers): ~1.9 GB, ~2.9 GB resident with an 8k context, fits any
    # ≥ 4 GB GPU and stays usable on CPU / Jetson. Set
    # OLLAMA_CHAT_MODEL=qwen2.5:7b-instruct-q4_K_M on ≥ 8 GB cards for a
    # +3-5 point confidence lift (Phase 12 measurements).
    chat_model: str = _env("OLLAMA_CHAT_MODEL", "qwen2.5:3b-instruct-q4_K_M")
    embed_model: str = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text")
    # Context window sent with every generate call. The diagnose and
    # plan_action prompts (instructions + runbooks + k8sgpt findings +
    # describe output + baselines) run past 2k tokens; Ollama silently drops
    # the START of an over-long prompt — the instructions and JSON schema —
    # and a 3b model then answers with free-form JSON that fails validation.
    # Older Ollama builds (e.g. 0.4.7) ignore OLLAMA_CONTEXT_LENGTH, so the
    # agent sets num_ctx per request. Every generate call must send the same
    # value, or Ollama reloads the model between calls.
    ollama_num_ctx: int = int(_env("OLLAMA_NUM_CTX", "8192"))

    kubeconfig: str = _env("KUBECONFIG", str(_REPO_ROOT / ".kube" / "config"))

    audit_hmac_key: str = _env("AUDIT_HMAC_KEY", "dev-key-replace-me")
    audit_log_path: str = _env("AUDIT_LOG_PATH", str(_REPO_ROOT / "agent" / "audit.log"))

    confidence_threshold: float = float(_env("AGENT_CONFIDENCE_THRESHOLD", "0.6"))
    log_level: str = _env("LOG_LEVEL", "INFO")

    qdrant_collection: str = _env("QDRANT_COLLECTION", "runbooks")
    agent_mode_name: str = _env("AGENT_MODE_NAME", "default")

    # Phase 13 — Prometheus URL the baseline learner polls. Empty
    # string disables the learner entirely (the graph's enrich node
    # then returns no snapshots and the diagnose prompt falls back to
    # raw values). Leaving the laptop-default in place so `make up`
    # autodetects when the Prometheus service is in the stack.
    prometheus_url: str = _env("PROMETHEUS_URL", "http://localhost:9090")
    baseline_scrape_interval_s: int = int(_env("BASELINE_SCRAPE_INTERVAL_S", "60"))
    baseline_fit_interval_s: int = int(_env("BASELINE_FIT_INTERVAL_S", "600"))


settings = Settings()


def kubectl_auth_args() -> list[str]:
    """Return the `--kubeconfig <path>` argv slice to splice into a
    kubectl invocation — but ONLY if the configured kubeconfig file
    actually exists on disk. In an in-cluster Pod the file is absent
    and kubectl auto-discovers the ServiceAccount token at
    `/var/run/secrets/kubernetes.io/serviceaccount`. On the laptop
    demo path the file exists and gets passed through.

    Centralising this here keeps every subprocess kubectl call site
    (api.post_mode, tools._kubectl, usecase_runner, chaos_observer,
    graph verify, synthetic_events…) consistent. Adding a new
    kubectl call in the agent codebase? Use this helper, not
    `--kubeconfig settings.kubeconfig` directly.
    """
    if settings.kubeconfig and os.path.isfile(settings.kubeconfig):
        return ["--kubeconfig", settings.kubeconfig]
    return []


def kubectl_env() -> dict[str, str]:
    """Environment for kubectl subprocesses. When no on-disk kubeconfig is
    in play (in-cluster: `kubectl_auth_args()` returns []), strip a stray
    `KUBECONFIG` from the environment so kubectl falls back to the
    ServiceAccount instead of HARD-FAILING on an unreadable baked path.

    This is the env-var counterpart to `kubectl_auth_args()`: that helper
    controls the `--kubeconfig` flag, but a `KUBECONFIG` env var bypasses
    it and reaches kubectl directly — which is exactly how a compose-era
    `KUBECONFIG=/root/.kube/config` baked into an image broke `isolate_pod`
    in-cluster (uid 1000 can't read /root, kubectl errors instead of using
    the ServiceAccount). Pass this as `env=` to every kubectl subprocess.
    """
    env = dict(os.environ)
    if not kubectl_auth_args():  # no usable on-disk kubeconfig → in-cluster
        env.pop("KUBECONFIG", None)
    return env
