"""Pull the LLM + embedding models into the running Ollama container.

Run via `make pull-models` (or .\\make.ps1 pull-models). Idempotent: skips
models that are already present in the local Ollama registry.

We deliberately use raw HTTP via urllib (stdlib only) so this script can run
BEFORE `uv sync` provisions the agent's dependencies.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODELS = [
    os.environ.get("OLLAMA_CHAT_MODEL", "qwen2.5:7b-instruct-q4_K_M"),
    os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text"),
]


def _get(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def _post_stream(url: str, payload: dict) -> int:
    """POST a streaming request, print one progress line per event. Returns http status."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    last_status = ""
    with urllib.request.urlopen(req, timeout=None) as resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            status = event.get("status", "")
            if status and status != last_status:
                # Don't spam every percent change; new status string is the signal.
                print(f"  {status}", flush=True)
                last_status = status
            if event.get("error"):
                print(f"  ERROR: {event['error']}", file=sys.stderr, flush=True)
                return 500
        return resp.status


def wait_for_ollama(max_seconds: int = 120) -> None:
    deadline = time.time() + max_seconds
    while time.time() < deadline:
        try:
            _get(f"{OLLAMA_HOST}/api/tags")
            return
        except urllib.error.URLError:
            time.sleep(2)
    raise SystemExit(f"Ollama not reachable at {OLLAMA_HOST} after {max_seconds}s")


def installed_models() -> set[str]:
    tags = _get(f"{OLLAMA_HOST}/api/tags")
    return {m["name"] for m in tags.get("models", [])}


def pull(model: str) -> None:
    print(f"Pulling {model} ...", flush=True)
    code = _post_stream(f"{OLLAMA_HOST}/api/pull", {"name": model, "stream": True})
    if code >= 400:
        raise SystemExit(f"pull failed for {model} (HTTP {code})")
    print(f"  done: {model}", flush=True)


def main() -> int:
    print(f"Waiting for Ollama at {OLLAMA_HOST} ...")
    wait_for_ollama()
    present = installed_models()
    for model in MODELS:
        if model in present:
            print(f"Already present: {model}")
            continue
        pull(model)
    print("All models present.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
