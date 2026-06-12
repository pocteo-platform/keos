#!/usr/bin/env bash
# Build a Falco legacy-eBPF probe for THIS host's kernel and push it as a
# tiny OCI image the chart's prebuilt-probe initContainer can pull.
#
# Why this exists: on BTF-less kernels (NVIDIA L4T/tegra, some locked-down
# kernels) Falco's modern_ebpf probe can't run, and the in-cluster
# falco-driver-loader can't COMPILE the legacy probe either — its Debian-12
# image can't exec the host's Ubuntu-24 kbuild tooling (glibc mismatch), so
# a feature-test silently fails and the probe miscompiles (see report §1.4c).
# Compiling natively on the target host with its real toolchain is reliable.
#
#   sudo REGISTRY=registry.fleet.local:5000 scripts/build-falco-probe.sh [--insecure]
#
# Produces:  ${REGISTRY}/pocteo-platform/falco-probe-$(uname -r):<falco-driver-ver>
# Consumed by: the chart's tier-2 prebuilt-probe values (install-edge.sh
#              security ladder). Rebuild after a kernel upgrade.
#
# Requirements on the host: docker, kernel headers for $(uname -r), clang,
# llvm, make. The falco driver SOURCE is extracted from the loader image so
# the probe version always matches the deployed Falco.

set -euo pipefail

FALCO_LOADER_IMAGE="${FALCO_LOADER_IMAGE:-falcosecurity/falco-driver-loader:0.40.0}"
KREL="$(uname -r)"
INSECURE=0
for a in "$@"; do [[ "$a" == "--insecure" ]] && INSECURE=1; done

step() { echo "[falco-probe] $*"; }
die()  { echo "[falco-probe] ✗ $*" >&2; exit 1; }

[[ -n "${REGISTRY:-}" ]] || die "REGISTRY is required (e.g. REGISTRY=registry.fleet.local:5000)"
command -v docker >/dev/null || die "docker required"
command -v make   >/dev/null || die "make required (apt install build-essential)"
command -v clang  >/dev/null || die "clang required (apt install clang llvm)"
[[ -d "/lib/modules/$KREL/build" ]] || die "kernel headers for $KREL missing (apt install linux-headers-$KREL)"

# Driver version baked into the loader image (e.g. 8.0.0+driver).
DRIVER_VER="$(docker run --rm --entrypoint sh "$FALCO_LOADER_IMAGE" -c 'ls /usr/src | grep -oE "falco-[0-9.]+\+driver" | head -1 | sed "s/falco-//"' 2>/dev/null)"
[[ -n "$DRIVER_VER" ]] || die "could not determine falco driver version from $FALCO_LOADER_IMAGE"
TAG="${DRIVER_VER//+/-}"
IMAGE="${REGISTRY}/pocteo-platform/falco-probe-${KREL}:${TAG}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

step "extracting driver source ($DRIVER_VER) from $FALCO_LOADER_IMAGE"
CID="$(docker create "$FALCO_LOADER_IMAGE")"
docker cp "$CID:/usr/src/falco-${DRIVER_VER}" "$WORK/src" >/dev/null
docker rm "$CID" >/dev/null

step "compiling probe for kernel $KREL (native toolchain)"
make -C "$WORK/src/bpf" KERNELDIR="/lib/modules/$KREL/build" >/dev/null 2>"$WORK/build.log" \
  || { tail -20 "$WORK/build.log" >&2; die "probe compile failed"; }
[[ -f "$WORK/src/bpf/probe.o" ]] || die "probe.o not produced"
# Sanity: the RSS_STAT feature gate is the one that silently breaks in-cluster.
grep -q "Setting HAS_RSS_STAT_ARRAY" "$WORK/build.log" \
  || echo "[falco-probe] ⚠ HAS_RSS_STAT_ARRAY not set — check kernel >=6.2 headers" >&2
step "probe.o built ($(stat -c%s "$WORK/src/bpf/probe.o") bytes)"

step "packaging + pushing $IMAGE"
cp "$WORK/src/bpf/probe.o" "$WORK/falco-bpf.o"
cat > "$WORK/Dockerfile" <<'EOF'
FROM busybox:stable
COPY falco-bpf.o /falco-bpf.o
EOF
PUSH_FLAGS=(); [[ "$INSECURE" -eq 1 ]] && PUSH_FLAGS+=(--output type=image,push=true,registry.insecure=true)
docker buildx build --platform "linux/$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')" \
  -t "$IMAGE" --push "${PUSH_FLAGS[@]}" "$WORK" >/dev/null \
  || docker build -t "$IMAGE" "$WORK" && docker push "$IMAGE"

echo "[falco-probe] ✓ pushed $IMAGE"
echo "[falco-probe]   install with: install-edge.sh sets this automatically when"
echo "[falco-probe]   preflight labels this node keos.pocteo/btf-kernel=false."
