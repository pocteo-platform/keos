#!/usr/bin/env bash
# Keos release pipeline — build, mirror and publish EVERYTHING a fleet
# device needs into a self-hosted registry. No CI required: run this from
# a workstation with docker (buildx), helm and skopeo.
#
#   REGISTRY=registry.fleet.local:5000 scripts/release.sh all
#
# Subcommands:
#   build        buildx multi-arch first-party images (+ Jetson ollama variant)
#   mirror       copy every pinned third-party image into ${REGISTRY},
#                preserving multi-arch manifest lists (skopeo)
#   chart        helm package + push the chart to oci://${REGISTRY}/charts
#   images-list  print the third-party image list (derived from the chart —
#                single source of truth, also consumed by build-airgap-bundle.sh)
#   verify       multi-arch audit of everything in ${REGISTRY} (scripts/audit-images.sh)
#   all          build + mirror + chart + verify
#
# Env / flags:
#   REGISTRY        (required) e.g. registry.fleet.local:5000
#   VERSION         first-party tag    (default: chart version from Chart.yaml)
#   PLATFORMS       default linux/amd64,linux/arm64
#   OLLAMA_VERSION  default 0.30.7 (the Jetson variant tracks this)
#   --insecure      plain-HTTP / self-signed registry: plumbs
#                   `helm --plain-http` + `skopeo --dest-tls-verify=false`
#
# Device-side registry trust (k3s registries.yaml): see docs/REGISTRY.md.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
CHART_DIR="$REPO_ROOT/deploy/helm/pocteo"
BAKE_FILE="$REPO_ROOT/deploy/buildx-bake.hcl"

PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"
OLLAMA_VERSION="${OLLAMA_VERSION:-0.30.7}"
INSECURE=0

step() { echo "[release] $*"; }
ok()   { echo "[release] ✓ $*"; }
warn() { echo "[release] ⚠ $*" >&2; }
die()  { echo "[release] ✗ $*" >&2; exit 1; }

# ─── Args ──────────────────────────────────────────────────────────────────
CMD=""
for a in "$@"; do
  case "$a" in
    --insecure) INSECURE=1 ;;
    build|mirror|chart|images-list|verify|all) CMD="$a" ;;
    -h|--help) sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $a (see --help)" ;;
  esac
done
[[ -n "$CMD" ]] || die "missing subcommand (build|mirror|chart|images-list|verify|all)"

# images-list works offline; everything else needs the registry.
if [[ "$CMD" != "images-list" ]]; then
  [[ -n "${REGISTRY:-}" ]] || die "REGISTRY is required, e.g. REGISTRY=registry.fleet.local:5000"
fi
VERSION="${VERSION:-$(grep -E '^version:' "$CHART_DIR/Chart.yaml" | awk '{print $2}')}"

SKOPEO_DEST_FLAGS=(); HELM_PUSH_FLAGS=()
if [[ "$INSECURE" -eq 1 ]]; then
  SKOPEO_DEST_FLAGS+=(--dest-tls-verify=false)
  HELM_PUSH_FLAGS+=(--plain-http)
fi

# ─── images-list ───────────────────────────────────────────────────────────
# Derive the third-party list FROM THE CHART (helm template with every
# optional component enabled) so it can never drift from values.yaml —
# the failure mode the old hardcoded array in build-airgap-bundle.sh had.
cmd_images_list() {
  command -v helm >/dev/null || die "helm not on PATH"
  {
    helm template keos "$CHART_DIR" \
      --set-string auth.apiToken=x --set-string auth.hmacKey=y \
      --set falco.enabled=true \
      --set falco.driver.kind=ebpf \
      --set falco.falcosidekick.enabled=true \
      --set tetragon.enabled=true \
      --set chaos-mesh.enabled=true \
      --set monitoring.enabled=true \
      --set agent.attachment.mode=hard \
      --set-string agent.attachment.nodeName=render-hook \
      2>/dev/null | grep -E '^\s+(-\s+)?image:' | sed -E 's/.*image:\s*"?([^" ]+)"?.*/\1/'
    # Not rendered by the chart but required by the install flow:
    echo "nvcr.io/nvidia/k8s-device-plugin:v0.16.2"   # keosctl gpu-setup
    echo "busybox:stable"                              # falco prebuilt-probe carrier
  } | grep -v '^ghcr.io/pocteo-platform/' \
    | grep -v '^appropriate/curl$' \
    | sort -u
  # Excluded: first-party (comes from `build`); appropriate/curl (untagged,
  # falcosidekick's `helm test` hook only — never pulled during install).
}

# Map a source image ref to its path inside ${REGISTRY}. Mirrors the
# convention install-edge.sh's apply_registry_overrides() expects.
dest_path() {
  local src="$1" path
  path="$src"
  case "$src" in
    ghcr.io/*|quay.io/*|nvcr.io/*|registry.k8s.io/*|docker.io/*) path="${src#*/}" ;;
  esac
  # Bare Docker-official images (caddy, nats, busybox…) live under library/.
  [[ "$path" == */* ]] || path="library/$path"
  echo "$path"
}

# ─── build ─────────────────────────────────────────────────────────────────
# IMPORTANT — QEMU + uv caveat (verified 2026-06-12 on an arm64 host):
#   `uv pip install` SEGFAULTS (exit 139) when run under QEMU emulation
#   (e.g. building the amd64 leg on an arm64 host, or vice-versa on some
#   hosts). The NATIVE leg always works. So the cross-arch leg must build
#   on real hardware of that arch, not emulation. Options:
#     * run this on an amd64 workstation, add the Jetson as a native arm64
#       node:  docker buildx create --name keosbuild --node ws --platform linux/amd64
#              docker buildx create --append --name keosbuild --node jetson \
#                 --platform linux/arm64 ssh://<user>@<jetson>
#              BUILDX_BUILDER=keosbuild  (then release.sh build)
#     * or build+push each arch on its own machine and `docker buildx
#       imagetools create` a combined manifest.
#   The native-arch images themselves are fine — this is purely an
#   emulation limitation. See docs/RELEASE_CHECKLIST.md.
cmd_build() {
  command -v docker >/dev/null || die "docker not on PATH"
  docker buildx version >/dev/null 2>&1 || die "docker buildx not available"
  local host_arch; host_arch="$(uname -m)"
  if { [[ "$PLATFORMS" == *amd64* && "$host_arch" == "aarch64" ]] || \
       [[ "$PLATFORMS" == *arm64* && "$host_arch" == "x86_64" ]]; }; then
    warn "cross-arch build requested on a $host_arch host — the emulated leg uses QEMU."
    warn "  uv segfaults under emulation; if a leg dies with exit 139, build that arch"
    warn "  natively (heterogeneous buildx builder / per-arch build). See the header note."
  fi
  step "Building first-party images (${PLATFORMS}) + Jetson ollama variant → ${REGISTRY}"
  REGISTRY="$REGISTRY" TAG="$VERSION" PLATFORMS="$PLATFORMS" OLLAMA_VERSION="$OLLAMA_VERSION" \
    docker buildx bake -f "$BAKE_FILE" all --push
  ok "pushed keos-api/keos-mcp-server/keos-frontend:$VERSION + keos-ollama-jetson:${OLLAMA_VERSION}-jp6"
}

# ─── mirror ────────────────────────────────────────────────────────────────
cmd_mirror() {
  # skopeo preserves multi-arch manifest lists; `docker pull && push`
  # flattens to the puller's arch and must never be used here.
  local copier=""
  if command -v skopeo >/dev/null; then copier=skopeo;
  elif docker buildx imagetools inspect --help >/dev/null 2>&1; then copier=imagetools;
  else die "need skopeo (preferred) or docker buildx imagetools"; fi

  local src dest
  while IFS= read -r src; do
    dest="${REGISTRY}/$(dest_path "$src")"
    step "mirror $src → $dest"
    if [[ "$copier" == skopeo ]]; then
      skopeo copy --all "${SKOPEO_DEST_FLAGS[@]}" "docker://$src" "docker://$dest" >/dev/null \
        || die "skopeo copy failed for $src"
    else
      docker buildx imagetools create -t "$dest" "$src" >/dev/null \
        || die "imagetools create failed for $src"
    fi
  done < <(cmd_images_list)
  ok "all third-party images mirrored into ${REGISTRY}"
}

# ─── chart ─────────────────────────────────────────────────────────────────
cmd_chart() {
  command -v helm >/dev/null || die "helm not on PATH"
  step "Packaging chart $VERSION + pushing to oci://${REGISTRY}/charts"
  local tmp; tmp="$(mktemp -d)"
  helm dependency update "$CHART_DIR" >/dev/null
  helm package "$CHART_DIR" -d "$tmp" >/dev/null
  helm push "${HELM_PUSH_FLAGS[@]}" "$tmp/pocteo-${VERSION}.tgz" "oci://${REGISTRY}/charts"
  rm -rf "$tmp"
  ok "chart pushed: oci://${REGISTRY}/charts/pocteo:${VERSION}"
}

# ─── verify ────────────────────────────────────────────────────────────────
cmd_verify() {
  REGISTRY="$REGISTRY" VERSION="$VERSION" OLLAMA_VERSION="$OLLAMA_VERSION" INSECURE="$INSECURE" \
    bash "$SCRIPT_DIR/audit-images.sh"
}

case "$CMD" in
  images-list) cmd_images_list ;;
  build)       cmd_build ;;
  mirror)      cmd_mirror ;;
  chart)       cmd_chart ;;
  verify)      cmd_verify ;;
  all)         cmd_build; cmd_mirror; cmd_chart; cmd_verify ;;
esac
