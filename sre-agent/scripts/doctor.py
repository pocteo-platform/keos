r"""Standalone diagnostic.

Run as `python -m scripts.doctor` (or via `make doctor` / `.\make.ps1 doctor`).
Hits the API's /api/health/detailed endpoint, pretty-prints each
component's status, and tells you exactly what to run for any failure.

Works both inside the api container and from the host (auto-discovers the
right base URL + bearer token from .env.local).

Exits 0 if everything is healthy, 1 otherwise — safe to use as a CI gate
or as the last line of `make install`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # noqa: BLE001
    print("ERROR: httpx not installed. Run 'uv sync' first.", file=sys.stderr)
    sys.exit(2)


def _read_token() -> str | None:
    # 1. Env var wins.
    tok = os.environ.get("SHP_API_TOKEN")
    if tok:
        return tok
    # 2. .env.local next to the repo root.
    here = Path(__file__).resolve().parent.parent
    env_local = here / ".env.local"
    if env_local.exists():
        for line in env_local.read_text(encoding="utf-8").splitlines():
            if line.startswith("SHP_API_TOKEN="):
                return line.split("=", 1)[1].strip()
    return None


def _base_url() -> str:
    # When running inside the api container, hit ourselves directly.
    if Path("/.dockerenv").exists():
        return "http://localhost:8000"
    # Otherwise default to Caddy on :80.
    return os.environ.get("KEOS_API_URL", "http://localhost")


def _sym(ok: bool) -> str:
    return "[OK] " if ok else "[--] "


def main() -> int:
    base = _base_url()
    token = _read_token()
    if not token:
        print("WARNING: no SHP_API_TOKEN found in env or .env.local. Run 'make up' first.")
        return 1

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{base}/api/health/detailed"

    print(f"Probing {url} ...")
    try:
        r = httpx.get(url, headers=headers, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        print(f"[--] API not reachable at {base}: {exc}")
        print("     Fix: run 'make up' (then 'make rebuild' if it was already up)")
        return 1

    if r.status_code == 401:
        print("[--] Bearer token rejected. Check SHP_API_TOKEN matches .env.local.")
        return 1
    if r.status_code != 200:
        print(f"[--] {r.status_code} from {url}: {r.text[:200]}")
        return 1

    data: dict[str, Any] = r.json()
    components: dict[str, dict[str, Any]] = data.get("components", {})

    print()
    print("=" * 64)
    print("  Keos SRE Agent — System Doctor")
    print("=" * 64)

    failures: list[tuple[str, dict[str, Any]]] = []
    for name in ("ollama", "qdrant", "nats", "opa", "cluster", "agent_mode"):
        c = components.get(name, {})
        ok = bool(c.get("ok"))
        label = name.upper().ljust(12)
        if ok:
            # Pretty-print the success detail.
            extras = {k: v for k, v in c.items() if k != "ok"}
            if extras:
                detail = ", ".join(f"{k}={v}" for k, v in extras.items())
                print(f"{_sym(True)} {label} {detail}")
            else:
                print(f"{_sym(True)} {label}")
        else:
            reason = c.get("reason", "unknown")
            fix = c.get("fix", "")
            print(f"{_sym(False)} {label} {reason}")
            if fix:
                print(f"             -> fix: {fix}")
            failures.append((name, c))

    # Phase 12 — calibration status (cluster-wide median, % above 90 %).
    # Informational only; never causes the doctor to exit non-zero.
    try:
        r2 = httpx.get(f"{base}/api/calibration", headers=headers, timeout=5.0)
        if r2.status_code == 200:
            cal = r2.json()
            n = cal.get("total", 0)
            median = cal.get("median_confidence")
            pct90 = cal.get("pct_above_90")
            label = "CALIBRATION ".ljust(12)
            if n == 0:
                print(f"{_sym(True)} {label} no incidents graded yet")
            else:
                median_s = f"{median:.2f}" if median is not None else "n/a"
                pct90_s = f"{pct90 * 100:.0f}%" if pct90 is not None else "n/a"
                print(
                    f"{_sym(True)} {label} rules={n} median={median_s} "
                    f"above_90={pct90_s}  (target ≥ 90%)"
                )
    except Exception:  # noqa: BLE001
        pass  # calibration is best-effort; don't fail the doctor on it

    # Phase 13 — baselines learner status. Informational; the doctor
    # reports cold-start ("learning") vs warm ("ok") + key count so the
    # operator knows when the LLM is being fed σ-above-baseline context.
    try:
        r3 = httpx.get(f"{base}/api/baselines", headers=headers, timeout=5.0)
        if r3.status_code == 200:
            b = r3.json()
            n_keys = b.get("keys_known", 0)
            n_samples = b.get("total_samples", 0)
            items = b.get("items", []) or []
            n_ok = sum(1 for it in items if it.get("status") == "ok")
            n_learning = sum(1 for it in items if it.get("status") == "learning")
            label = "BASELINES   ".ljust(12)
            if n_keys == 0:
                print(
                    f"{_sym(True)} {label} no Prometheus samples yet "
                    f"(learner warming or PROMETHEUS_URL unset)"
                )
            else:
                print(
                    f"{_sym(True)} {label} keys={n_keys} samples={n_samples} "
                    f"fitted={n_ok} learning={n_learning}"
                )
    except Exception:  # noqa: BLE001
        pass  # baselines best-effort

    print()
    if not failures:
        print("All systems go. You can run a use case from the UI.")
        return 0

    print(f"{len(failures)} issue(s) need attention. Most common path to green:")
    print("  1. make up         # bring compose services online")
    print("  2. make cluster    # create k3d cluster + apply CRDs")
    print("  3. make seed       # populate the runbook RAG store")
    print("  4. make doctor     # re-run this check")
    return 1


if __name__ == "__main__":
    sys.exit(main())
