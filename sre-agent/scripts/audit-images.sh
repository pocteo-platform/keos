#!/usr/bin/env bash
# Multi-arch + pin audit for every image the chart references. The manual
# replacement for a CI guard: run before/after `release.sh mirror` so an
# amd64-only image can never ship to an arm64 fleet silently (the class of
# bug that broke opa on the Jetson — bare tags are amd64-only).
#
#   scripts/audit-images.sh                      # audit UPSTREAM sources
#   REGISTRY=host:5000 scripts/audit-images.sh   # audit the fleet registry copies
#                                                #  (+ first-party images)
# Env: REGISTRY, VERSION (first-party tag), OLLAMA_VERSION, INSECURE=1.
# Exit code = number of failures (CI/cron friendly).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
VERSION="${VERSION:-$(grep -E '^version:' "$REPO_ROOT/deploy/helm/pocteo/Chart.yaml" | awk '{print $2}')}"
OLLAMA_VERSION="${OLLAMA_VERSION:-0.30.7}"
INSECURE="${INSECURE:-0}"
FAILURES=0

ok()   { echo "[audit] ✓ $*"; }
fail() { echo "[audit] ✗ $*" >&2; FAILURES=$((FAILURES+1)); }
warn() { echo "[audit] ⚠ $*" >&2; }

# Images that are intentionally single-arch.
EXEMPT_ARM64_ONLY=("keos-ollama-jetson")

# Map upstream ref → fleet-registry path (same convention as release.sh).
dest_path() {
  local src="$1" path="$1"
  case "$src" in
    ghcr.io/*|quay.io/*|nvcr.io/*|registry.k8s.io/*|docker.io/*) path="${src#*/}" ;;
  esac
  [[ "$path" == */* ]] || path="library/$path"
  echo "$path"
}

# Return the platform list of an image ref, one "os/arch" per line.
platforms_of() {
  local ref="$1" raw=""
  if command -v skopeo >/dev/null; then
    local flags=()
    [[ "$INSECURE" == "1" && -n "${REGISTRY:-}" && "$ref" == "${REGISTRY}"* ]] && flags+=(--tls-verify=false)
    raw="$(skopeo inspect "${flags[@]}" --raw "docker://$ref" 2>/dev/null)" || return 1
  else
    raw="$(docker manifest inspect "$ref" 2>/dev/null)" || return 1
  fi
  python3 - "$raw" <<'EOF'
import json, sys
d = json.loads(sys.argv[1])
ms = d.get("manifests")
if ms:  # manifest list / OCI index
    for m in ms:
        p = m.get("platform", {})
        if p.get("os") and p.get("architecture") != "unknown":
            print(f"{p['os']}/{p['architecture']}")
else:   # single-arch manifest: arch not in the manifest itself
    print("single")
EOF
}

check_multiarch() {
  local ref="$1" plats
  # One retry with backoff — Docker Hub rate-limits anonymous manifest
  # requests and a transient 429 must not read as "image missing".
  if ! plats="$(platforms_of "$ref")"; then
    sleep 3
    plats="$(platforms_of "$ref")" || {
      fail "$ref — cannot inspect (missing, unreachable, or registry rate limit — rerun later or docker login)"
      return
    }
  fi
  for ex in "${EXEMPT_ARM64_ONLY[@]}"; do
    if [[ "$ref" == *"$ex"* ]]; then
      if echo "$plats" | grep -q "linux/arm64"; then ok "$ref (arm64-only by design)"; else fail "$ref — exempt image missing arm64"; fi
      return
    fi
  done
  if [[ "$plats" == "single" ]]; then
    fail "$ref — single-arch manifest (no manifest list); arm64 devices cannot pull it"
  elif echo "$plats" | grep -q "linux/amd64" && echo "$plats" | grep -q "linux/arm64"; then
    ok "$ref"
  else
    fail "$ref — missing arch (has: $(echo "$plats" | tr '\n' ' '))"
  fi
}

# ─── 1. The chart-derived third-party list ─────────────────────────────────
echo "[audit] auditing third-party images $( [[ -n "${REGISTRY:-}" ]] && echo "in ${REGISTRY}" || echo "upstream" )…"
while IFS= read -r src; do
  ref="$src"
  [[ -n "${REGISTRY:-}" ]] && ref="${REGISTRY}/$(dest_path "$src")"
  check_multiarch "$ref"
done < <(bash "$SCRIPT_DIR/release.sh" images-list)

# ─── 2. First-party images (registry mode only) ────────────────────────────
if [[ -n "${REGISTRY:-}" ]]; then
  for img in keos-api keos-mcp-server keos-frontend; do
    check_multiarch "${REGISTRY}/pocteo-platform/${img}:${VERSION}"
  done
  check_multiarch "${REGISTRY}/pocteo-platform/keos-ollama-jetson:${OLLAMA_VERSION}-jp6"
fi

# ─── 3. Pin sanity in values.yaml ──────────────────────────────────────────
if grep -A3 'repository: openpolicyagent/opa' "$REPO_ROOT/deploy/helm/pocteo/values.yaml" \
     | grep -E '^\s*tag:' | grep -q -- '-static"'; then
  ok "opa tag is -static (multi-arch)"
else
  fail "opa tag in values.yaml is not -static — bare opa tags are amd64-only"
fi

# ─── 4. Dockerfile lint — no hardcoded arch in download URLs ───────────────
if grep -rEn 'amd64|x86_64' "$REPO_ROOT/agent/Dockerfile.api" "$REPO_ROOT/mcp_server/Dockerfile" "$REPO_ROOT/frontend/Dockerfile" 2>/dev/null; then
  fail "hardcoded amd64/x86_64 in a first-party Dockerfile (breaks arm64 — the Jetson kubectl bug class)"
else
  ok "no hardcoded arch in first-party Dockerfiles"
fi

echo "[audit] done — $FAILURES failure(s)"
exit "$FAILURES"
