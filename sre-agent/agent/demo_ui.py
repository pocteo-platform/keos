"""make demo-ui helper.

Sequence:
  1. Wait until /api/health on http://localhost responds.
  2. Open the SPA in the default browser.
  3. Read SHP_API_TOKEN from .env.local.
  4. Wait 5 seconds (so the user has time to paste the token).
  5. POST /api/scenarios/run for scenario 1 so the user lands on a live pipeline.

Idempotent and safe to ctrl-c at any point. Failures are logged but do not
crash the demo — the user can still drive the UI manually.
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_LOCAL = REPO_ROOT / ".env.local"
BASE_URL = os.environ.get("SHP_DEMO_BASE", "http://localhost")


def _wait_for_api(timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    print(f"[demo-ui] waiting for {BASE_URL}/api/health ...")
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/api/health", timeout=2)
            if r.status_code == 200:
                print(f"[demo-ui] API is up: {r.json()}")
                return True
        except httpx.RequestError:
            pass
        time.sleep(1)
    print(f"[demo-ui] API never came up at {BASE_URL}. Check `docker compose ps`.")
    return False


def _read_token() -> str | None:
    if not ENV_LOCAL.exists():
        return None
    for line in ENV_LOCAL.read_text(encoding="utf-8").splitlines():
        if line.startswith("SHP_API_TOKEN="):
            return line.split("=", 1)[1].strip()
    return None


def _trigger_scenario(token: str, scenario_id: int = 1) -> None:
    try:
        r = httpx.post(
            f"{BASE_URL}/api/scenarios/run",
            headers={"Authorization": f"Bearer {token}"},
            json={"scenario_id": scenario_id},
            timeout=10,
        )
        if r.status_code == 200:
            print(f"[demo-ui] scenario {scenario_id} dispatched: {r.json()}")
        else:
            print(f"[demo-ui] scenario dispatch returned HTTP {r.status_code}: {r.text}")
    except httpx.RequestError as exc:
        print(f"[demo-ui] scenario dispatch failed: {exc}")


def main() -> int:
    if not _wait_for_api():
        return 1

    print(f"[demo-ui] opening {BASE_URL} in your browser ...")
    try:
        webbrowser.open(BASE_URL, new=2)
    except Exception as exc:  # noqa: BLE001
        print(f"[demo-ui] couldn't open browser: {exc}; open {BASE_URL} manually")

    token = _read_token()
    if token is None:
        print(f"[demo-ui] no SHP_API_TOKEN in {ENV_LOCAL}; skipping auto-scenario.")
        print("[demo-ui] paste the token shown above into the SPA's login screen.")
        return 0

    print("[demo-ui] waiting 5s for you to paste the token in the login screen ...")
    time.sleep(5)
    _trigger_scenario(token, scenario_id=1)
    print("[demo-ui] done. Navigate to Pipeline -> Live to watch the agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
