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

    chat_model: str = _env("OLLAMA_CHAT_MODEL", "qwen2.5:3b-instruct-q4_K_M")
    embed_model: str = _env("OLLAMA_EMBED_MODEL", "nomic-embed-text")

    kubeconfig: str = _env("KUBECONFIG", str(_REPO_ROOT / ".kube" / "config"))

    audit_hmac_key: str = _env("AUDIT_HMAC_KEY", "dev-key-replace-me")
    audit_log_path: str = _env("AUDIT_LOG_PATH", str(_REPO_ROOT / "agent" / "audit.log"))

    confidence_threshold: float = float(_env("AGENT_CONFIDENCE_THRESHOLD", "0.6"))
    log_level: str = _env("LOG_LEVEL", "INFO")

    qdrant_collection: str = _env("QDRANT_COLLECTION", "runbooks")
    agent_mode_name: str = _env("AGENT_MODE_NAME", "default")


settings = Settings()
