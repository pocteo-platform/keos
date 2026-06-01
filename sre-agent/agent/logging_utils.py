"""Logging configuration shared across all CLI entrypoints.

Two formats:

  - `text` (default for local `make demo` / `python -m agent.graph --once`):
    one-line `HH:MM:SS LEVEL logger :: message`, easy to eyeball.

  - `json` (default in containers): one JSON object per line, parsable by
    Loki / Datadog / OpenSearch without grok patterns. Standard fields
    (`ts`, `level`, `logger`, `message`) plus any `extra=` dict passed at
    log time.

Pick the format via the `LOG_FORMAT` env var. When unset, we default to
`json` if we look like we're in a container (presence of `/.dockerenv`)
and `text` otherwise — so the same code base does the right thing in
both worlds without explicit wiring.

`LOG_LEVEL` controls verbosity (default INFO).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def _default_format() -> str:
    explicit = (os.environ.get("LOG_FORMAT") or "").strip().lower()
    if explicit in ("json", "text"):
        return explicit
    # Heuristic: containerised runs ship JSON; bare-metal runs ship text.
    return "json" if Path("/.dockerenv").exists() else "text"


def _build_handler(fmt: str) -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    if fmt == "json":
        try:
            from pythonjsonlogger import jsonlogger
        except ImportError:
            # python-json-logger not installed (e.g. someone running the
            # test suite without --extras). Fall back to text so we don't
            # crash the boot.
            return _build_handler("text")
        # `rename_fields` makes the output ECS-friendly without depending
        # on ECS-aware loggers downstream.
        handler.setFormatter(
            jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={
                    "asctime": "ts",
                    "levelname": "level",
                    "name": "logger",
                    "message": "msg",
                },
                # ISO-8601 with milliseconds, UTC. Avoids local-timezone
                # ambiguity when logs are shipped to a central store.
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
    return handler


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Idempotent logging setup.

    Honors:
      - LOG_LEVEL env (default INFO) — overridable via `level` arg.
      - LOG_FORMAT env (json | text) — overridable via `fmt` arg.
    """
    chosen_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    chosen_fmt = (fmt or _default_format()).lower()
    root = logging.getLogger()
    if getattr(root, "_shp_configured", False):
        root.setLevel(chosen_level)
        return
    root.handlers = [_build_handler(chosen_fmt)]
    root.setLevel(chosen_level)
    # Quiet noisy upstream libs.
    for noisy in ("httpx", "httpcore", "urllib3", "kubernetes.client.rest"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._shp_configured = True  # type: ignore[attr-defined]
