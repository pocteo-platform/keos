"""Bearer-token auth for the FastAPI app.

PoC-grade but production-ready secret hygiene: the API token can come from
either `SHP_API_TOKEN` (env var, used by the laptop docker-compose path) OR
`SHP_API_TOKEN_FILE` (path to a file, used by the Helm chart so a Kubernetes
`Secret` projected-volume mount works without baking the secret into the
container env).

The HMAC audit key and this token are intentionally separate concerns — the
audit key signs a forensic stream regardless of who is talking; this token
authenticates the operator at the API boundary.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Final

from fastapi import HTTPException, Request, status

log = logging.getLogger(__name__)

TOKEN_ENV: Final = "SHP_API_TOKEN"
TOKEN_FILE_ENV: Final = "SHP_API_TOKEN_FILE"

_GENERATED_PREFIX = "dev-"


def get_token() -> str:
    """Resolve the configured token.

    Resolution order:
      1. `SHP_API_TOKEN_FILE` — path to a file (K8s Secret projected-volume
         mount). Re-read on every call so a rotation via `kubectl edit secret`
         takes effect within ~1 minute (kubelet projects the new value on
         disk; no restart required).
      2. `SHP_API_TOKEN` env var (laptop / docker-compose path).
      3. Generated dev token (test fallback only — changes on every restart,
         breaks the SPA's stored token). Loud warning so it's obvious
         when this branch is active.
    """
    file_path = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if file_path:
        try:
            tok = Path(file_path).read_text(encoding="utf-8").strip()
            if tok:
                return tok
            log.warning(
                "SHP_API_TOKEN_FILE=%s exists but is empty; falling back to SHP_API_TOKEN env",
                file_path,
            )
        except OSError as exc:
            log.warning(
                "SHP_API_TOKEN_FILE=%s unreadable (%s); falling back to SHP_API_TOKEN env",
                file_path,
                exc,
            )

    tok = os.environ.get(TOKEN_ENV, "").strip()
    if tok:
        return tok

    tok = _GENERATED_PREFIX + secrets.token_urlsafe(16)
    os.environ[TOKEN_ENV] = tok
    log.warning(
        "No SHP_API_TOKEN or SHP_API_TOKEN_FILE configured; generated a "
        "throwaway dev token. The SPA will need to be re-paired on every "
        "restart. Set SHP_API_TOKEN_FILE for production."
    )
    return tok


async def require_bearer(request: Request) -> None:
    """FastAPI dependency: 401 unless the Authorization header matches.

    Uses constant-time comparison via `secrets.compare_digest` so token
    length / prefix can't be inferred from response timing.
    """
    expected = get_token()
    header = request.headers.get("authorization", "")
    parts = header.split(maxsplit=1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(parts[1], expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def check_ws_token(token: str | None) -> bool:
    """Compare the WebSocket query-param token to the configured one.
    Constant-time so it can't leak whether the prefix matched."""
    return bool(token) and secrets.compare_digest(token or "", get_token())
