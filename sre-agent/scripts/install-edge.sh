#!/usr/bin/env bash
# Keos SRE Agent — workstation-driven edge installer.
#
# Designed for immutable edge OSes — Talos, Kairos, Flatcar — where
# the node is managed by API (Talos) or cloud-init (Kairos) rather
# than by SSHing in and editing files. The script runs on the
# OPERATOR's workstation (laptop or jump host). It talks to the
# cluster via kubectl using whatever kubeconfig is set in the
# environment.
#
# Works against:
#   - Talos (single-node + multi-node) — containerd socket at
#     /run/containerd/containerd.sock
#   - Kairos (immutable Linux for edge K8s) — bundles k3s by default,
#     so containerd socket at /run/k3s/containerd/containerd.sock
#   - vanilla k3s, k3d, RKE2, kubeadm, EKS / GKE / AKS (anywhere
#     kubectl works)
#
# What it does:
#   1. Probe the cluster: distro, K8s version, GPU capacity, node names.
#   2. Generate bearer token + audit HMAC key (openssl-random, 32 bytes).
#   3. Detect the target node (default: the GPU-bearing node, fallback
#      to the first ready node, override with --node).
#   4. Run `helm upgrade --install` with sensible edge defaults
#      (GPU on if available, node-pinned hard via the bootstrap-label
#      hook, observe-only mode for safety on first install).
#   5. Wait for all pods Ready (~3 min cold-start).
#   6. Pull the LLM models via the in-cluster Ollama pod's exec
#      (the slow step — ~5-10 min on first install).
#   7. Seed the RAG corpus via the api pod's exec.
#   8. Print the URL + token the operator pastes into the SPA.
#
# Idempotent — re-running it does `helm upgrade --install`, a no-op
# if nothing changed. If a step fails the script prints the failing
# command + exits non-zero; nothing's left half-broken.
#
# Usage:
#   # Default — auto-detect everything
#   bash install-edge.sh
#
#   # Explicit kubeconfig (talosctl-fetched, for example)
#   bash install-edge.sh --kubeconfig ~/.kube/talos-jetson.yaml
#
#   # Pin to a specific node by name
#   bash install-edge.sh --node keos-jetson-2
#
#   # Start in active mode (skip observe-only)
#   bash install-edge.sh --mode active
#
#   # CPU-only (force, for testing without GPU)
#   bash install-edge.sh --no-gpu
#
#   # Build images locally + import into the cluster's containerd
#   # (auto-falls-back when the public ghcr.io images aren't pullable —
#   # e.g. the org's packages are still set to private. Skip the probe
#   # + force the build with --local-build; skip the auto-fallback
#   # with --no-local-build-fallback).
#   bash install-edge.sh --local-build
#
#   # Probe-only — show what the script would install, then exit.
#   # Useful for verifying the kubeconfig + distro detection before
#   # committing to a real install.
#   bash install-edge.sh --probe-only
#
# Companion docs:
#   docs/JETSON_FIRST_BOOT.md — end-to-end walkthrough with Talos +
#     Kairos kubeconfig-fetch examples.

set -euo pipefail

# ─── Defaults ──────────────────────────────────────────────────────────────

# v1.0.0+ ships as an OCI artifact at ghcr.io/pocteo-platform/charts/pocteo.
# Helm 3.8+ installs OCI charts directly: `helm install <release> oci://...`
# — no `helm repo add` needed. Override with KEOS_CHART_NAME if you mirror
# the chart elsewhere; override KEOS_CHART_VERSION to pin a specific tag.
CHART_REPO=""   # unused for OCI; kept for compat with older versions
CHART_NAME="${KEOS_CHART_NAME:-oci://ghcr.io/pocteo-platform/charts/pocteo}"
CHART_VERSION="${KEOS_CHART_VERSION:-1.0.0}"
RELEASE="${KEOS_RELEASE:-keos}"
NAMESPACE="${KEOS_NAMESPACE:-keos}"
KUBECONFIG_OVERRIDE=""
NODE_OVERRIDE=""
AGENT_MODE="observe-only"
ENABLE_GPU="auto"
SKIP_MODEL_PULL=0
PROBE_ONLY=0
PRINT_HELP=0
AIRGAP=0
IMAGE_REGISTRY=""
LOCAL_BUILD=0
LOCAL_BUILD_AUTO=1     # default: auto-fall-back to local build if public images aren't reachable
LOCAL_CHART_PATH="${KEOS_LOCAL_CHART:-}"   # for `make install-edge` against k3d

# ─── Argparse ──────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kubeconfig)  KUBECONFIG_OVERRIDE="$2"; shift 2 ;;
    --node)        NODE_OVERRIDE="$2"; shift 2 ;;
    --mode)        AGENT_MODE="$2"; shift 2 ;;
    --release)     RELEASE="$2"; shift 2 ;;
    --namespace)   NAMESPACE="$2"; shift 2 ;;
    --no-gpu)      ENABLE_GPU="false"; shift ;;
    --gpu)         ENABLE_GPU="true"; shift ;;
    --skip-models) SKIP_MODEL_PULL=1; shift ;;
    --probe-only)  PROBE_ONLY=1; shift ;;
    --airgap)      AIRGAP=1; SKIP_MODEL_PULL=1; shift ;;
    --image-registry) IMAGE_REGISTRY="$2"; shift 2 ;;
    --local-build) LOCAL_BUILD=1; LOCAL_BUILD_AUTO=0; shift ;;
    --no-local-build-fallback) LOCAL_BUILD_AUTO=0; shift ;;
    --local-chart) LOCAL_CHART_PATH="$2"; shift 2 ;;
    -h|--help)     PRINT_HELP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "$PRINT_HELP" -eq 1 ]]; then
  sed -n '3,52p' "$0" | sed 's/^# \?//'
  exit 0
fi

if [[ -n "$KUBECONFIG_OVERRIDE" ]]; then
  export KUBECONFIG="$KUBECONFIG_OVERRIDE"
fi

# Local-chart mode: install from a local directory instead of the repo.
# Useful for `make install-edge` against the k3d dev cluster, and for
# air-gapped Talos installs where the operator brings a tarball.
if [[ -n "$LOCAL_CHART_PATH" ]]; then
  CHART_NAME="$LOCAL_CHART_PATH"
fi

# ─── UI helpers ────────────────────────────────────────────────────────────

if [[ -t 1 ]]; then
  C_DIM=$(printf '\033[2m'); C_RST=$(printf '\033[0m')
  C_OK=$(printf '\033[32m'); C_WARN=$(printf '\033[33m'); C_ERR=$(printf '\033[31m')
  C_HEAD=$(printf '\033[1;36m')
else
  C_DIM=""; C_RST=""; C_OK=""; C_WARN=""; C_ERR=""; C_HEAD=""
fi

step()  { echo "${C_HEAD}[install-edge]${C_RST} $*"; }
ok()    { echo "${C_HEAD}[install-edge]${C_RST} ${C_OK}✓${C_RST} $*"; }
warn()  { echo "${C_HEAD}[install-edge]${C_RST} ${C_WARN}⚠ $*${C_RST}"; }
die()   { echo "${C_HEAD}[install-edge]${C_RST} ${C_ERR}✗ $*${C_RST}" >&2; exit 1; }

# ─── Preflight ─────────────────────────────────────────────────────────────

step "Preflight…"
command -v kubectl >/dev/null || die "kubectl not on PATH"
command -v python3 >/dev/null || die "python3 not on PATH"
if [[ "$PROBE_ONLY" -eq 0 ]]; then
  # helm + openssl are only needed for the actual install; probe-only
  # can run with just kubectl + python3 (handy for CI smoke tests).
  command -v helm    >/dev/null || die "helm not on PATH (apt install helm OR https://helm.sh/docs/intro/install)"
  command -v openssl >/dev/null || die "openssl not on PATH"
fi

if ! kubectl version --request-timeout=5s >/dev/null 2>&1; then
  cat >&2 <<EOF
kubectl can't reach a cluster. Set up your kubeconfig first:

  Talos:   talosctl --talosconfig ./talosconfig --nodes <ip> kubeconfig ~/.kube/jetson.yaml
           export KUBECONFIG=~/.kube/jetson.yaml
  Kairos:  ssh kairos@<ip> 'cat /etc/rancher/k3s/k3s.yaml' \\
               | sed "s/127.0.0.1/<ip>/" > ~/.kube/jetson.yaml
           export KUBECONFIG=~/.kube/jetson.yaml
  k3s:     scp <user>@<ip>:/etc/rancher/k3s/k3s.yaml ~/.kube/jetson.yaml
           sed -i "s/127.0.0.1/<ip>/" ~/.kube/jetson.yaml
           export KUBECONFIG=~/.kube/jetson.yaml

Re-run this script after \`kubectl get nodes\` works.
EOF
  exit 1
fi
if [[ "$PROBE_ONLY" -eq 1 ]]; then
  ok "kubectl + python3 OK (probe-only — skipping helm + openssl)"
else
  ok "kubectl + helm + openssl + python3 OK"
fi

# ─── Cluster context probe ────────────────────────────────────────────────

step "Probing cluster…"
K8S_VERSION=$(kubectl version -o json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null \
  || echo "unknown")
NODES_RAW=$(kubectl get nodes -o json)
NODE_COUNT=$(echo "$NODES_RAW" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["items"]))')

# Distro detection — mirrors the agent's _detect_distro logic so the
# install-time view matches what the dashboard will report. Kairos
# bundles k3s by default, so a Kairos node usually reports as
# "kairos+k3s" (we promote to "kairos" when we see the Kairos labels).
DISTRO="kubeadm"
if [[ "$K8S_VERSION" == *"k3s"* ]]; then DISTRO="k3s"; fi
if [[ "$K8S_VERSION" == *"rke2"* ]]; then DISTRO="rke2"; fi
NODE_FINGERPRINT=$(printf '%s' "$NODES_RAW" | python3 -c '
import sys, json
data = json.load(sys.stdin)
flags = set()
for n in data["items"]:
    labels = (n.get("metadata") or {}).get("labels") or {}
    osimg = ((n.get("status") or {}).get("nodeInfo") or {}).get("osImage") or ""
    for k, v in labels.items():
        key = k.lower(); val = str(v).lower()
        if "talos" in key or "talos" in val: flags.add("talos")
        if "kairos" in key or "kairos" in val: flags.add("kairos")
    o = osimg.lower()
    if "talos" in o: flags.add("talos")
    if "kairos" in o: flags.add("kairos")
print(",".join(sorted(flags)))
')
case ",$NODE_FINGERPRINT," in
  *",talos,"*)  DISTRO="talos" ;;
  *",kairos,"*) DISTRO="kairos" ;;
esac

# GPU detection — first node that advertises nvidia.com/gpu.
GPU_NODE=$(printf '%s' "$NODES_RAW" | python3 -c '
import sys, json
data = json.load(sys.stdin)
for n in data["items"]:
    cap = (n.get("status") or {}).get("capacity") or {}
    if int(cap.get("nvidia.com/gpu", 0) or 0) > 0:
        print(n["metadata"]["name"]); sys.exit(0)
')

# Node target: explicit > GPU-bearing > first node.
if [[ -n "$NODE_OVERRIDE" ]]; then
  NODE="$NODE_OVERRIDE"
elif [[ -n "$GPU_NODE" ]]; then
  NODE="$GPU_NODE"
else
  NODE=$(echo "$NODES_RAW" | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["items"][0]["metadata"]["name"])')
fi

if ! kubectl get node "$NODE" >/dev/null 2>&1; then
  die "Node '$NODE' not found. Run 'kubectl get nodes' to see the list."
fi

# Resolve ENABLE_GPU=auto.
if [[ "$ENABLE_GPU" == "auto" ]]; then
  ENABLE_GPU=$( [[ -n "$GPU_NODE" ]] && echo "true" || echo "false" )
fi

# Distro-specific runtime class name. Talos installs nvidia via its
# System Extension; Kairos via its `nvidia-l4t` bundle. Both land as
# the `nvidia` RuntimeClass (same name everywhere with the device
# plugin); we keep one constant for now.
GPU_RUNTIME_CLASS="nvidia"

# Distro-specific containerd socket path. Chaos Mesh's chaos-daemon
# needs this. Talos uses the OS-default; k3s + Kairos (which bundles
# k3s by default) use the k3s-specific path. RKE2 uses its own.
case "$DISTRO" in
  k3s|kairos) CONTAINERD_SOCKET="/run/k3s/containerd/containerd.sock" ;;
  rke2)       CONTAINERD_SOCKET="/run/k3s/containerd/containerd.sock" ;;
  *)          CONTAINERD_SOCKET="/run/containerd/containerd.sock" ;;
esac

ok "distro: $DISTRO · k8s: $K8S_VERSION · nodes: $NODE_COUNT"
ok "target node: $NODE · GPU: $([[ "$ENABLE_GPU" == "true" ]] && echo "yes (nvidia.com/gpu)" || echo "no — CPU mode")"
case "$DISTRO" in
  talos)  ok "Talos detected — chaos-daemon socket $CONTAINERD_SOCKET" ;;
  kairos) ok "Kairos detected (k3s under the hood) — chaos-daemon socket $CONTAINERD_SOCKET" ;;
  k3s)    ok "k3s detected — chaos-daemon socket $CONTAINERD_SOCKET" ;;
esac

if [[ "$PROBE_ONLY" -eq 1 ]]; then
  cat <<EOF

${C_HEAD}---${C_RST} ${C_DIM}probe-only mode — exiting before any change${C_RST} ${C_HEAD}---${C_RST}

  Helm install would run:
    helm upgrade --install ${RELEASE} ${CHART_NAME} \\
      --namespace ${NAMESPACE} --create-namespace \\
      --set agent.attachment.mode=hard \\
      --set agent.attachment.nodeName=${NODE} \\
      --set agent.bootstrapNodeLabel.enabled=true \\
      --set chaos-mesh.chaosDaemon.socketPath=${CONTAINERD_SOCKET} \\
      $( [[ "$ENABLE_GPU" == "true" ]] && echo "--set ollama.gpu.enabled=true \\
      --set ollama.gpu.runtimeClassName=${GPU_RUNTIME_CLASS}" )

  Drop --probe-only to execute it.
EOF
  exit 0
fi

# ─── Token + HMAC key generation ──────────────────────────────────────────

step "Generating bearer token + audit HMAC key…"
API_TOKEN=$(openssl rand -base64 32 | tr -d '=' | head -c 32)
HMAC_KEY=$(openssl rand -base64 32)
TOKEN_CACHE="${HOME}/.keos-token"
umask 0077
mkdir -p "$(dirname "$TOKEN_CACHE")"
printf '%s\n' "$API_TOKEN" > "$TOKEN_CACHE"
ok "tokens ready (operator copy cached at $TOKEN_CACHE)"

# ─── Helm install ──────────────────────────────────────────────────────────

if [[ "$AIRGAP" -eq 1 ]]; then
  # Airgap mode: refuse to reach the public chart repo OR docker.io.
  # Operator MUST supply a packaged chart (--local-chart) and a
  # registry mirror (--image-registry) that holds the 12 stock + 3
  # first-party images. Validates both before any side effect.
  [[ -n "$LOCAL_CHART_PATH" ]] || die "--airgap requires --local-chart <path>"
  [[ -n "$IMAGE_REGISTRY"  ]] || die "--airgap requires --image-registry <host:port>"
  ok "airgap mode — using local chart $LOCAL_CHART_PATH + registry $IMAGE_REGISTRY"
elif [[ -z "$LOCAL_CHART_PATH" ]]; then
  ok "using OCI chart $CHART_NAME (version $CHART_VERSION) — no helm repo add needed"
else
  ok "using local chart at $LOCAL_CHART_PATH (skip repo)"
fi

# ─── Public-image probe + local-build fallback ─────────────────────────────
#
# If the operator passed --local-build, OR if the published first-party
# images at ghcr.io/pocteo-platform/keos-* aren't pullable (private
# package or no network), we build the 3 images from the repo source +
# import them into the cluster's containerd. Saves the operator from
# fighting GHCR package visibility for a first-time install.
#
# How we import varies by distro:
#   - k3s:    docker save | sudo k3s ctr -n k8s.io image import -
#   - kairos: same as k3s (Kairos bundles k3s by default)
#   - other:  spin up a tiny localhost:5000 registry container, push to it,
#             set imageRegistry to host.docker.internal:5000 / localhost:5000
#
# Skipped entirely in --airgap mode (the operator brings their own
# pre-built images via --image-registry).

REPO_ROOT=""
if [[ "$AIRGAP" -eq 0 ]]; then
  # Find the repo root: the directory ABOVE wherever this script lives,
  # but only if it looks like the Keos repo (has agent/Dockerfile.api).
  _script_dir="$(cd "$(dirname "$0")" && pwd)"
  _candidate="$(dirname "$_script_dir")"
  if [[ -f "$_candidate/agent/Dockerfile.api" ]]; then
    REPO_ROOT="$_candidate"
  fi
fi

_probe_public_images() {
  # Quick anonymous-pull probe against the registry's token endpoint.
  # Returns 0 if pull would succeed, non-zero on 401/403/etc.
  local img="${1:-ghcr.io/pocteo-platform/keos-api}"
  local repo="${img#*/}"   # strip registry, keep org/name
  curl -fsSL --max-time 5 \
    "https://ghcr.io/token?scope=repository%3A${repo}%3Apull&service=ghcr.io" \
    -o /dev/null 2>/dev/null
}

if [[ "$AIRGAP" -eq 0 && "$LOCAL_BUILD" -eq 0 && "$LOCAL_BUILD_AUTO" -eq 1 ]]; then
  step "Probing whether public first-party images are pullable…"
  if _probe_public_images; then
    ok "public images reachable — using ghcr.io/pocteo-platform/keos-*"
  else
    warn "ghcr.io/pocteo-platform/keos-* not anonymously pullable (likely private package). Falling back to --local-build."
    LOCAL_BUILD=1
  fi
fi

if [[ "$LOCAL_BUILD" -eq 1 ]]; then
  [[ -n "$REPO_ROOT" ]] || die "--local-build needs a Keos repo clone — run this script from inside the cloned repo's scripts/ directory"
  command -v docker >/dev/null || die "--local-build requires docker on PATH to build images"
  step "Building keos-api / keos-mcp-server / keos-frontend from $REPO_ROOT…"
  LOCAL_TAG="local-$(date +%Y%m%d-%H%M%S)"
  docker build -t "keos-api:$LOCAL_TAG"        -f "$REPO_ROOT/agent/Dockerfile.api"     "$REPO_ROOT" >/dev/null
  docker build -t "keos-mcp-server:$LOCAL_TAG" -f "$REPO_ROOT/mcp_server/Dockerfile"    "$REPO_ROOT" >/dev/null
  docker build -t "keos-frontend:$LOCAL_TAG"   -f "$REPO_ROOT/frontend/Dockerfile"      "$REPO_ROOT/frontend" >/dev/null
  ok "images built locally (tag=$LOCAL_TAG)"

  # Import into the cluster's containerd. We detect the cluster's shape
  # so the right import tool gets used:
  #
  #   kind:   kind load docker-image <img>:<tag> --name <cluster>
  #           (kind clusters run their own containerd inside a Docker
  #           container; the host's `ctr` can't reach it directly)
  #   k3d:    k3d image import <img>:<tag> -c <cluster>
  #           (same containerd-in-container shape as kind)
  #   k3s:    sudo k3s ctr -n=k8s.io image import <tar>
  #           (native single-machine setup — Jetson, Kairos, plain k3s)
  #   other:  sudo ctr -n=k8s.io image import <tar>
  #           (any host with a kubelet sharing the host containerd)
  step "Importing images into the cluster's containerd…"
  KIND_CLUSTER=""
  K3D_CLUSTER=""
  _ctx="$(kubectl config current-context 2>/dev/null || true)"
  if command -v kind >/dev/null 2>&1 && [[ "$_ctx" == kind-* ]]; then
    KIND_CLUSTER="${_ctx#kind-}"
  elif command -v k3d >/dev/null 2>&1 && [[ "$_ctx" == k3d-* ]]; then
    K3D_CLUSTER="${_ctx#k3d-}"
  fi
  for img in keos-api keos-mcp-server keos-frontend; do
    if [[ -n "$KIND_CLUSTER" ]]; then
      kind load docker-image "$img:$LOCAL_TAG" --name "$KIND_CLUSTER" >/dev/null
    elif [[ -n "$K3D_CLUSTER" ]]; then
      k3d image import "$img:$LOCAL_TAG" -c "$K3D_CLUSTER" >/dev/null
    else
      docker save "$img:$LOCAL_TAG" -o "/tmp/$img-$LOCAL_TAG.tar"
      if command -v k3s >/dev/null 2>&1; then
        sudo k3s ctr -n=k8s.io image import "/tmp/$img-$LOCAL_TAG.tar" >/dev/null
      elif command -v ctr >/dev/null 2>&1; then
        sudo ctr -n=k8s.io image import "/tmp/$img-$LOCAL_TAG.tar" >/dev/null
      else
        die "no image-import tool available — install kind/k3d/k3s/ctr, or pass --image-registry <host:port> and push images yourself."
      fi
      rm -f "/tmp/$img-$LOCAL_TAG.tar"
    fi
    ok "imported $img:$LOCAL_TAG"
  done
fi

step "Installing Keos to namespace '$NAMESPACE'…"
HELM_ARGS=(
  upgrade --install "$RELEASE" "$CHART_NAME"
  --namespace "$NAMESPACE" --create-namespace
  --set-string "auth.apiToken=$API_TOKEN"
  --set-string "auth.hmacKey=$HMAC_KEY"
  --set "agent.defaultMode=$AGENT_MODE"
  --set "agent.attachment.mode=hard"
  --set-string "agent.attachment.nodeName=$NODE"
  --set "agent.bootstrapNodeLabel.enabled=true"
  --wait --timeout 10m
)
# OCI charts: helm pulls + installs by version. Local-chart path
# embeds the version in the .tgz filename so this is a no-op.
if [[ "$CHART_NAME" == oci://* ]]; then
  HELM_ARGS+=(--version "$CHART_VERSION")
fi
# Local-build: point the chart at the images we just imported into
# containerd. global.imageRegistry="" + per-image repository overrides
# bypass the chart's default ghcr.io/pocteo-platform prefix.
# imagePullPolicy=Never tells kubelet not to try ghcr.io at all — the
# image is already in containerd's local store.
if [[ "$LOCAL_BUILD" -eq 1 ]]; then
  HELM_ARGS+=(
    --set-string "global.imageRegistry="
    --set-string "api.image.repository=keos-api"
    --set-string "api.image.tag=$LOCAL_TAG"
    --set-string "api.image.pullPolicy=Never"
    --set-string "mcpServer.image.repository=keos-mcp-server"
    --set-string "mcpServer.image.tag=$LOCAL_TAG"
    --set-string "mcpServer.image.pullPolicy=Never"
    --set-string "frontend.image.repository=keos-frontend"
    --set-string "frontend.image.tag=$LOCAL_TAG"
    --set-string "frontend.image.pullPolicy=Never"
  )
fi
# Chaos Mesh's chaos-daemon needs the containerd socket path for the
# detected distro. The chart's sub-chart values.yaml accepts these as
# top-level keys. We always set them — even when they match the
# chart's default — so what's running is exactly what was probed.
HELM_ARGS+=(
  --set "chaos-mesh.chaosDaemon.socketPath=$CONTAINERD_SOCKET"
  --set-string "chaos-mesh.chaosDaemon.runtime=containerd"
)
# Airgap: every image gets prefixed by the operator's registry mirror.
# First-party images (api/mcp/frontend) use global.imageRegistry (which
# the chart's `pocteo.image` helper applies only to firstParty=true).
# Stock images need explicit per-image repo overrides because they're
# pulled verbatim from values.yaml.
if [[ "$AIRGAP" -eq 1 ]]; then
  HELM_ARGS+=(
    --set-string "global.imageRegistry=${IMAGE_REGISTRY}/pocteo-platform"
    --set-string "caddy.image.repository=${IMAGE_REGISTRY}/library/caddy"
    --set-string "nats.image.repository=${IMAGE_REGISTRY}/library/nats"
    --set-string "ollama.image.repository=${IMAGE_REGISTRY}/ollama/ollama"
    --set-string "qdrant.image.repository=${IMAGE_REGISTRY}/qdrant/qdrant"
    --set-string "opa.image.repository=${IMAGE_REGISTRY}/openpolicyagent/opa"
    --set-string "chaos-mesh.images.registry=${IMAGE_REGISTRY}"
    --set-string "agent.bootstrapNodeLabel.image=${IMAGE_REGISTRY}/alpine/k8s:1.30.0"
  )
fi
if [[ "$ENABLE_GPU" == "true" ]]; then
  HELM_ARGS+=(
    --set "ollama.gpu.enabled=true"
    --set-string "ollama.gpu.runtimeClassName=$GPU_RUNTIME_CLASS"
  )
fi
helm "${HELM_ARGS[@]}"
ok "Helm release '$RELEASE' deployed"

# ─── Model pull ────────────────────────────────────────────────────────────

if [[ "$SKIP_MODEL_PULL" -eq 0 ]]; then
  step "Pulling LLM models (slow — ~5-10 min on first install)…"
  OLLAMA_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/name=ollama \
                                          -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
  if [[ -z "$OLLAMA_POD" ]]; then
    warn "ollama pod not found yet; re-run with --skip-models if it still doesn't appear"
  else
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- ollama pull qwen2.5:3b-instruct-q4_K_M
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- ollama pull nomic-embed-text
    ok "models cached on $NODE"
  fi
fi

# ─── Runbook seed ──────────────────────────────────────────────────────────

step "Seeding the runbook corpus into Qdrant…"
API_POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/component=api \
                                  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")
if [[ -n "$API_POD" ]]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python -m agent.seed_rag 2>&1 | tail -3 || \
    warn "seed_rag returned non-zero; the agent still works, RAG just won't enrich diagnose"
  ok "runbooks indexed"
else
  warn "api pod not found; you'll need to seed manually later"
fi

# ─── Reachability ─────────────────────────────────────────────────────────

URL="(set ingress.host or use \`kubectl port-forward svc/caddy 8080:80 -n $NAMESPACE\`)"
INGRESS_HOST=$(kubectl get ingress -n "$NAMESPACE" -o jsonpath='{.items[0].spec.rules[0].host}' 2>/dev/null || echo "")
if [[ -n "$INGRESS_HOST" ]]; then
  URL="http://$INGRESS_HOST"
elif kubectl get svc caddy -n "$NAMESPACE" -o jsonpath='{.spec.type}' 2>/dev/null | grep -q NodePort; then
  NODE_PORT=$(kubectl get svc caddy -n "$NAMESPACE" -o jsonpath='{.spec.ports[0].nodePort}')
  NODE_IP=$(kubectl get node "$NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
  URL="http://$NODE_IP:$NODE_PORT"
elif kubectl get svc caddy -n "$NAMESPACE" >/dev/null 2>&1; then
  NODE_IP=$(kubectl get node "$NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
  URL="http://$NODE_IP   (after \`kubectl port-forward svc/caddy 80:80 -n $NAMESPACE\`)"
fi

# ─── Final summary ─────────────────────────────────────────────────────────

cat <<EOF

${C_HEAD}==================================================================
  Keos SRE Agent is up on $DISTRO.
==================================================================${C_RST}

  Open in your browser:
    ${C_OK}${URL}${C_RST}

  Bearer token (paste it on the login screen):
    ${C_OK}${API_TOKEN}${C_RST}

  Cached at:  $TOKEN_CACHE   (operator's local copy)

  Helm release:  ${RELEASE}  ·  namespace: ${NAMESPACE}
  Pinned to:     ${NODE} (mode=hard, label=keos.pocteo/sre-home=true)
  Agent mode:    ${AGENT_MODE} ${C_DIM}(flip to 'active' from Settings → Agent)${C_RST}

${C_DIM}Diagnose: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=api --tail=50${C_RST}
${C_DIM}Doctor:   curl -H 'Authorization: Bearer \$(cat ~/.keos-token)' ${URL}/api/health/detailed${C_RST}

EOF
