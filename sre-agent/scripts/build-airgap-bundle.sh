#!/usr/bin/env bash
# Build a single tarball containing everything an airgapped operator
# needs to install Keos v1.0.0 on a Kairos / Talos / k3s Jetson:
#
#   keos-airgap-v1.0.0.tar
#   ├── images/                  # docker save tarballs for every image
#   │   ├── pocteo-api.tar
#   │   ├── pocteo-mcp-server.tar
#   │   ├── pocteo-frontend.tar
#   │   ├── caddy.tar
#   │   ├── nats.tar
#   │   ├── ollama.tar
#   │   ├── opa.tar
#   │   ├── qdrant.tar
#   │   ├── alpine-k8s.tar
#   │   ├── chaos-mesh.tar       # 3 chaos-mesh images concatenated
#   │   └── manifest.txt
#   ├── chart/                   # `helm package` of deploy/helm/pocteo
#   │   └── keos-sre-agent-1.0.0.tgz
#   ├── scripts/                 # the install scripts the operator runs
#   │   ├── install-edge.sh
#   │   ├── load-airgap-images.sh
#   │   └── seed-ollama-models.sh
#   ├── models/                  # pre-pulled Ollama model blobs (~2.2 GB)
#   │   ├── qwen2.5-3b-instruct-q4_K_M.tar
#   │   └── nomic-embed-text.tar
#   └── README.md                # 1-page operator runbook
#
# On the airgapped Jetson the operator:
#   1. `tar xf keos-airgap-v1.0.0.tar`
#   2. `bash scripts/load-airgap-images.sh <local-registry-host>:5000`
#   3. `bash scripts/install-edge.sh --airgap --local-chart ./chart/keos-sre-agent-1.0.0.tgz`
#   4. `bash scripts/seed-ollama-models.sh`
#
# Total size on disk: ~5.5 GB (most of it Ollama model + ML/Python deps in api image).
# Designed to fit on a 16 GB USB stick with room for the Kairos OS image too.

set -euo pipefail

VERSION="${1:-1.1.0}"
OUTDIR="${OUTDIR:-/tmp/keos-airgap}"
TARBALL="${TARBALL:-keos-airgap-v${VERSION}.tar}"

# First-party images we build from source.
FIRST_PARTY=(
  "ghcr.io/pocteo-platform/keos-api:${VERSION}"
  "ghcr.io/pocteo-platform/keos-mcp-server:${VERSION}"
  "ghcr.io/pocteo-platform/keos-frontend:${VERSION}"
)

# Stock third-party images the chart pulls — DERIVED FROM THE CHART via
# `release.sh images-list` (helm template), so this bundle can never
# drift from what helm install actually requests. The old hardcoded
# array here shipped stale pins (ollama 0.4.7, opa without -static).
mapfile -t STOCK < <(bash "$(dirname "$0")/release.sh" images-list)

# Ollama models to pre-pull. Saved as Ollama blobs so the seed script
# can `ollama load <tarball>` straight into the Jetson's Ollama pod
# without re-downloading.
OLLAMA_MODELS=(
  "qwen2.5:3b-instruct-q4_K_M"
  "nomic-embed-text:latest"
)

step()  { echo "[bundle] $*"; }
ok()    { echo "[bundle] ✓ $*"; }
die()   { echo "[bundle] ✗ $*" >&2; exit 1; }

# ─── 0. Preflight ─────────────────────────────────────────────────────────

command -v docker >/dev/null || die "docker not on PATH"
command -v helm   >/dev/null || die "helm not on PATH"
command -v tar    >/dev/null || die "tar not on PATH"

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -d "$REPO_ROOT/deploy/helm/pocteo" ]] || die "run from a Keos repo clone"

rm -rf "$OUTDIR"
mkdir -p "$OUTDIR"/{images,chart,scripts,models}

# ─── 1. Pull (or build) every image ───────────────────────────────────────

step "Pulling stock images…"
for img in "${STOCK[@]}"; do
  docker pull "$img" || die "failed to pull $img"
  ok "$img"
done

step "Pulling first-party images (assumes you've already built + pushed
   them to ghcr.io; if not, run \`make build-images\` first)…"
for img in "${FIRST_PARTY[@]}"; do
  if ! docker pull "$img" 2>/dev/null; then
    echo "  ⚠ $img not in registry — checking local cache"
    docker image inspect "$img" >/dev/null 2>&1 \
      || die "missing $img — build it via \`make build-images\` first"
  fi
  ok "$img"
done

# ─── 2. Save every image to a tarball ─────────────────────────────────────

step "Saving images…"
ALL_IMAGES=("${FIRST_PARTY[@]}" "${STOCK[@]}")
for img in "${ALL_IMAGES[@]}"; do
  # Filename: replace / and : with -, e.g. ghcr.io/chaos-mesh/chaos-daemon:v2.7.2
  #   → ghcr-io-chaos-mesh-chaos-daemon-v2.7.2.tar
  fname="$(echo "$img" | tr '/:' '--').tar"
  docker save "$img" -o "$OUTDIR/images/$fname"
done
# Manifest so load-airgap-images.sh knows what to re-tag.
printf '%s\n' "${ALL_IMAGES[@]}" > "$OUTDIR/images/manifest.txt"
ok "saved $(echo "${ALL_IMAGES[*]}" | wc -w) images to $OUTDIR/images/"

# ─── 3. Pre-pull Ollama models into a temp container, then export ─────────

step "Pre-pulling Ollama models (slow — ~5-10 min for qwen2.5:3b)…"
docker run -d --rm --name keos-ollama-prefetch -v keos-airgap-models:/root/.ollama \
  ollama/ollama:0.4.7 serve >/dev/null
# Wait for ready.
until docker exec keos-ollama-prefetch ollama ps >/dev/null 2>&1; do sleep 2; done
for m in "${OLLAMA_MODELS[@]}"; do
  docker exec keos-ollama-prefetch ollama pull "$m" || die "pull $m failed"
  ok "model $m cached"
done
# Tar the entire ollama models volume so the seed script can restore it
# straight into the Jetson's Ollama pod's PVC.
docker run --rm -v keos-airgap-models:/data -v "$OUTDIR/models:/out" \
  alpine tar czf /out/ollama-models.tar.gz -C /data .
docker stop keos-ollama-prefetch >/dev/null 2>&1 || true
ok "models saved to $OUTDIR/models/ollama-models.tar.gz"

# ─── 4. Package the Helm chart ────────────────────────────────────────────

step "Packaging Helm chart…"
helm dependency update "$REPO_ROOT/deploy/helm/pocteo" 2>&1 | tail -2
helm package "$REPO_ROOT/deploy/helm/pocteo" -d "$OUTDIR/chart/" 2>&1 | tail -1
ok "chart packaged"

# ─── 5. Copy install scripts ──────────────────────────────────────────────

cp "$REPO_ROOT/scripts/install-edge.sh" "$OUTDIR/scripts/"
cat > "$OUTDIR/scripts/load-airgap-images.sh" <<'LOADER'
#!/usr/bin/env bash
# Load every image from ../images/*.tar into the local Docker daemon,
# re-tag against the supplied registry mirror, and push.
#
# Usage:
#   bash load-airgap-images.sh <mirror-host>:<port>
#   e.g. bash load-airgap-images.sh registry.internal:5000
set -euo pipefail
MIRROR="${1:?usage: $0 <mirror-host>:<port>}"
DIR="$(cd "$(dirname "$0")/../images" && pwd)"
while read -r img; do
  fname="$(echo "$img" | tr '/:' '--').tar"
  echo "  loading $img…"
  docker load -i "$DIR/$fname"
  # Re-tag under the mirror (strip registry prefix → use last 2 path components).
  short="$(echo "$img" | sed 's|^[^/]*/||')"
  docker tag "$img" "$MIRROR/$short"
  docker push "$MIRROR/$short"
done < "$DIR/manifest.txt"
echo "✓ all images loaded + pushed to $MIRROR"
LOADER
chmod +x "$OUTDIR/scripts/load-airgap-images.sh"

cat > "$OUTDIR/scripts/seed-ollama-models.sh" <<'SEEDER'
#!/usr/bin/env bash
# Copy the pre-fetched Ollama models tarball into the running Ollama
# pod's PVC. Run after `helm install` brings Ollama up.
set -euo pipefail
NS="${1:-keos}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "Copying models into namespace $NS Ollama pod…"
kubectl -n "$NS" cp "$DIR/models/ollama-models.tar.gz" "$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=ollama -o jsonpath='{.items[0].metadata.name}'):/tmp/"
kubectl -n "$NS" exec sts/ollama -- sh -c 'cd /root/.ollama && tar xzf /tmp/ollama-models.tar.gz && rm /tmp/ollama-models.tar.gz'
echo "✓ models in place; \`ollama list\` should show them now"
SEEDER
chmod +x "$OUTDIR/scripts/seed-ollama-models.sh"

# ─── 6. Operator README ───────────────────────────────────────────────────

cat > "$OUTDIR/README.md" <<README
# Keos airgap bundle v${VERSION}

Everything you need to install the Keos SRE Agent on an airgapped
edge Kubernetes cluster (Kairos / Talos / k3s on Jetson, RPi,
or any conformant K8s node).

## Contents

| Path | What it is | Size |
|---|---|---|
| \`images/*.tar\`  | All 12 OCI images saved via \`docker save\`. | ~3 GB |
| \`chart/keos-sre-agent-${VERSION}.tgz\`    | Helm chart with chaos-mesh sub-chart vendored. | ~150 KB |
| \`scripts/install-edge.sh\`                | Workstation installer; use with --airgap + --local-chart. | — |
| \`scripts/load-airgap-images.sh\`          | Loads images + re-tags + pushes to your mirror. | — |
| \`scripts/seed-ollama-models.sh\`          | Restores pre-pulled Ollama models into the running pod. | — |
| \`models/ollama-models.tar.gz\`            | Pre-pulled qwen2.5:3b + nomic-embed-text. | ~2.2 GB |
| \`README.md\`                              | This file. | — |

## Operator runbook

\`\`\`bash
# 1. Move the tarball to a host that can reach your registry mirror.
tar xf keos-airgap-v${VERSION}.tar
cd keos-airgap-v${VERSION}

# 2. Push every image into your mirror (replace registry.internal:5000).
bash scripts/load-airgap-images.sh registry.internal:5000

# 3. From the workstation that has kubectl access to the edge cluster,
#    install Keos using the local chart + the mirror.
export KUBECONFIG=~/.kube/jetson.yaml
bash scripts/install-edge.sh --airgap \\
  --local-chart ./chart/keos-sre-agent-${VERSION}.tgz \\
  --image-registry registry.internal:5000

# 4. Restore the Ollama models so the agent's LLM can answer
#    diagnose calls without trying to ollama.ai.
bash scripts/seed-ollama-models.sh
\`\`\`

That's it. \`make doctor\`-equivalent at \`/api/health/detailed\`
should be all green.

## Total time on the airgapped side

- Image loading: ~3 min
- Helm install + wait: ~3 min
- Model restore: ~30 s

Compare to the public-internet install (~25 min mostly waiting for
the Ollama model pull). Airgap is FASTER end-to-end if the bundle
was pre-built somewhere with internet.
README

# ─── 7. Tar it all up ─────────────────────────────────────────────────────

cd "$(dirname "$OUTDIR")"
tar cf "$TARBALL" "$(basename "$OUTDIR")"
SIZE=$(du -sh "$TARBALL" | awk '{print $1}')

echo ""
echo "============================================================"
echo "  Bundle: $(pwd)/$TARBALL"
echo "  Size:   $SIZE"
echo "  Move to your offline machine + extract."
echo "============================================================"
