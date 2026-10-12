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
#   # STANDARD FLEET FLOW — self-hosted registry populated by
#   # `scripts/release.sh all REGISTRY=<host:port>`. The chart AND every
#   # image come from the registry: no repo clone, no docker, no ghcr.io
#   # access needed on the device. Add --plain-http for a TLS-less registry
#   # (device-side containerd trust: see docs/OPERATIONS.md).
#   bash install-edge.sh --image-registry registry.fleet.local:5000 [--gpu]
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
#   # Security sensors — Falco + Tetragon for live eBPF runtime-threat
#   # detection (reverse shells, miners, priv-esc, exfil) — are installed
#   # BY DEFAULT (both are eBPF agents and reuse the detected containerd
#   # socket path). They need a capable kernel (BTF/CO-RE); on WSL2 or
#   # locked-down kernels they may not start. Opt out with:
#   bash install-edge.sh --no-security
#
#   # Probe-only — show what the script would install, then exit.
#   # Useful for verifying the kubeconfig + distro detection before
#   # committing to a real install.
#   bash install-edge.sh --probe-only
#
# Companion docs:
#   docs/INSTALLATION.md — end-to-end walkthrough with Talos +
#     Kairos kubeconfig-fetch examples.

set -euo pipefail

# ─── Defaults ──────────────────────────────────────────────────────────────

# v1.0.0+ ships as an OCI artifact at ghcr.io/pocteo-platform/charts/pocteo.
# Helm 3.8+ installs OCI charts directly: `helm install <release> oci://...`
# — no `helm repo add` needed. Override with KEOS_CHART_NAME if you mirror
# the chart elsewhere; override KEOS_CHART_VERSION to pin a specific tag.
CHART_REPO=""   # unused for OCI; kept for compat with older versions
CHART_NAME="${KEOS_CHART_NAME:-oci://ghcr.io/pocteo-platform/charts/pocteo}"
CHART_VERSION="${KEOS_CHART_VERSION:-1.1.0}"
HELM_PLAIN_HTTP=0   # --plain-http: registry without TLS (helm OCI pulls)
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
ENABLE_SECURITY=1     # security sensors (Falco + Tetragon) install by default; --no-security opts out
SEED_DEMO_WORKLOADS=1 # seed the demo namespaces + nginx-demo so use-cases have a live target; --no-demo-workloads opts out
SKIP_PREFLIGHT=0      # --skip-preflight skips the full scripts/preflight.sh suite (quick checks always run)
CONTAINERD_SOCKET_OVERRIDE=""  # --containerd-socket overrides the distro-probed path
GITOPS=0               # --gitops: bootstrap Flux pull-delivery instead of helm upgrade (Phase 23)
GITOPS_AGE_KEY=""      # --age-key <file>: fleet age PRIVATE key for SOPS decryption
GITOPS_CONFIG_TAG=""   # --config-tag <tag>: the keos-config artifact tag to reconcile

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
    --plain-http)  HELM_PLAIN_HTTP=1; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --containerd-socket) CONTAINERD_SOCKET_OVERRIDE="$2"; shift 2 ;;
    --local-build) LOCAL_BUILD=1; LOCAL_BUILD_AUTO=0; shift ;;
    --no-local-build-fallback) LOCAL_BUILD_AUTO=0; shift ;;
    --local-chart) LOCAL_CHART_PATH="$2"; shift 2 ;;
    --security)    ENABLE_SECURITY=1; shift ;;   # explicit (default); kept for back-compat
    --no-security|--skip-security) ENABLE_SECURITY=0; shift ;;
    --no-demo-workloads|--skip-demo-workloads) SEED_DEMO_WORKLOADS=0; shift ;;
    --gitops)      GITOPS=1; shift ;;
    --age-key)     GITOPS_AGE_KEY="$2"; shift 2 ;;
    --config-tag)  GITOPS_CONFIG_TAG="$2"; shift 2 ;;
    -h|--help)     PRINT_HELP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "$PRINT_HELP" -eq 1 ]]; then
  sed -n '3,71p' "$0" | sed 's/^# \?//'
  exit 0
fi

if [[ -n "$KUBECONFIG_OVERRIDE" ]]; then
  export KUBECONFIG="$KUBECONFIG_OVERRIDE"
fi

# ─── Resolve ONE kubeconfig and export it ──────────────────────────────────
# `kubectl` here is usually the k3s symlink, which silently falls back to
# /etc/rancher/k3s/k3s.yaml — but `helm` (a standalone binary) does NOT, so
# kubectl can reach the cluster while helm dies with "localhost:8080". Pin a
# single readable kubeconfig and EXPORT it so every tool (kubectl, helm,
# child processes) agrees. Honours an already-set KUBECONFIG first.
if [[ -z "${KUBECONFIG:-}" ]]; then
  for _kc in "$HOME/.kube/config" "/etc/rancher/k3s/k3s.yaml" "/etc/rancher/rke2/rke2.yaml"; do
    if [[ -r "$_kc" ]]; then
      export KUBECONFIG="$_kc"
      break
    fi
  done
fi

# ─── GitOps bootstrap (Phase 23) ────────────────────────────────────────────
# --gitops swaps the push-based `helm upgrade` for a one-time Flux bootstrap;
# after this the cluster PULLS the keos-config artifact from the private
# registry and reconciles itself. Delegates to `keosctl gitops bootstrap`
# (the single implementation) with the resolved registry + kubeconfig.
if [[ "$GITOPS" -eq 1 ]]; then
  [[ -n "$IMAGE_REGISTRY" ]] || { echo "--gitops requires --image-registry <host:5000>" >&2; exit 2; }
  [[ -n "$GITOPS_AGE_KEY" ]] || { echo "--gitops requires --age-key <fleet.agekey>" >&2; exit 2; }
  [[ -n "$GITOPS_CONFIG_TAG" ]] || { echo "--gitops requires --config-tag <tag> (e.g. ${CHART_VERSION}-edge)" >&2; exit 2; }
  _gitops_args=(gitops bootstrap --registry "$IMAGE_REGISTRY"
    --age-key "$GITOPS_AGE_KEY" --config-tag "$GITOPS_CONFIG_TAG")
  [[ "$HELM_PLAIN_HTTP" -eq 1 ]] && _gitops_args+=(--plain-http)
  exec bash "$(cd "$(dirname "$0")" && pwd)/keosctl" "${_gitops_args[@]}"
fi
# Verify with helm too — not just the k3s kubectl fallback — so a config
# kubectl can read but helm can't is caught here, not 10 minutes into helm.
if [[ "$PROBE_ONLY" -eq 0 ]] && command -v helm >/dev/null 2>&1; then
  if ! helm version >/dev/null 2>&1 || ! helm list -n "$NAMESPACE" >/dev/null 2>&1; then
    die "helm can't reach the cluster via KUBECONFIG='${KUBECONFIG:-<unset>}'.
   kubectl may work (k3s auto-finds /etc/rancher/k3s/k3s.yaml) but helm needs an
   explicit, readable kubeconfig. Fix one of:
     • run as the user that owns ~/.kube/config (not via sudo/root), or
     • export KUBECONFIG=\$HOME/.kube/config before installing, or
     • sudo chmod 644 /etc/rancher/k3s/k3s.yaml  (single-user edge box), or
     • pass --kubeconfig <path>."
  fi
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
note()  { echo "${C_HEAD}[install-edge]${C_RST} ${C_DIM}$*${C_RST}"; }
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
# --containerd-socket overrides the probe for exotic layouts.
if [[ -n "$CONTAINERD_SOCKET_OVERRIDE" ]]; then
  CONTAINERD_SOCKET="$CONTAINERD_SOCKET_OVERRIDE"
else
  case "$DISTRO" in
    k3s|kairos) CONTAINERD_SOCKET="/run/k3s/containerd/containerd.sock" ;;
    rke2)       CONTAINERD_SOCKET="/run/k3s/containerd/containerd.sock" ;;
    *)          CONTAINERD_SOCKET="/run/containerd/containerd.sock" ;;
  esac
fi

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

# ─── Full preflight suite ──────────────────────────────────────────────────
# Runs the shared checker (memory vs model, node arch vs registry, kernel
# BTF labelling for the security ladder, registry reachability, …) when
# preflight.sh sits next to this script (repo clone / full download). A
# bare curl'd install-edge.sh still works — the quick checks above
# already ran. Opt out with --skip-preflight.
_pf_lib="$(cd "$(dirname "$0")" && pwd)/preflight.sh"
if [[ "$SKIP_PREFLIGHT" -eq 0 && -r "$_pf_lib" ]]; then
  step "Preflight (full suite)…"
  _pf_args=(--node "$NODE")
  [[ "$ENABLE_GPU" == "true"  ]] && _pf_args+=(--gpu)
  [[ "$ENABLE_GPU" == "false" ]] && _pf_args+=(--no-gpu)
  [[ "$LOCAL_BUILD" -eq 1     ]] && _pf_args+=(--local-build)
  [[ -n "$IMAGE_REGISTRY"     ]] && _pf_args+=(--image-registry "$IMAGE_REGISTRY")
  [[ "$HELM_PLAIN_HTTP" -eq 1 ]] && _pf_args+=(--insecure)
  [[ -n "$CONTAINERD_SOCKET_OVERRIDE" ]] && _pf_args+=(--containerd-socket "$CONTAINERD_SOCKET_OVERRIDE")
  [[ -n "${KEOS_CHAT_MODEL:-}" ]] && _pf_args+=(--model "$KEOS_CHAT_MODEL")
  if ! bash "$_pf_lib" "${_pf_args[@]}"; then
    die "preflight failed — fix the ✗ items above (or re-run with --skip-preflight to proceed anyway)"
  fi
elif [[ "$SKIP_PREFLIGHT" -eq 1 ]]; then
  warn "full preflight skipped (--skip-preflight)"
else
  note "scripts/preflight.sh not found next to this script — quick checks only."
  note "  For the full suite (memory/arch/BTF/registry): clone the repo or run 'keosctl preflight'."
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

# ─── Fleet-registry chart resolution ────────────────────────────────────────
# The standard production flow: a self-hosted registry populated by
# `scripts/release.sh all` serves the chart AND every image. When the
# operator passed --image-registry (non-airgap, no --local-chart), try
# oci://<registry>/charts/pocteo first — if it's there, no repo clone, no
# docker, no ghcr.io access is needed on the device at all.
if [[ "$AIRGAP" -eq 0 && -n "$IMAGE_REGISTRY" && -z "$LOCAL_CHART_PATH" ]]; then
  step "Probing fleet registry for the Helm chart…"
  _helm_probe_flags=()
  [[ "$HELM_PLAIN_HTTP" -eq 1 ]] && _helm_probe_flags+=(--plain-http)
  if helm show chart "${_helm_probe_flags[@]}" \
       "oci://${IMAGE_REGISTRY}/charts/pocteo" --version "$CHART_VERSION" \
       >/dev/null 2>&1; then
    CHART_NAME="oci://${IMAGE_REGISTRY}/charts/pocteo"
    LOCAL_BUILD_AUTO=0   # registry serves the images too — no ghcr probe / local-build fallback
    ok "chart found: ${CHART_NAME} (version $CHART_VERSION) — fleet-registry install"
  else
    warn "no chart at oci://${IMAGE_REGISTRY}/charts/pocteo:$CHART_VERSION — falling back to the usual chart sources."
    warn "  (populate the registry with: REGISTRY=${IMAGE_REGISTRY} keosctl release all)"
  fi
fi

if [[ "$AIRGAP" -eq 0 && "$LOCAL_BUILD" -eq 0 && "$LOCAL_BUILD_AUTO" -eq 1 ]]; then
  step "Probing whether public first-party images are pullable…"
  if _probe_public_images; then
    ok "public images reachable — using ghcr.io/pocteo-platform/keos-*"
  else
    warn "ghcr.io/pocteo-platform/keos-* not anonymously pullable (likely private package). Falling back to --local-build."
    LOCAL_BUILD=1
  fi
fi

# Same trick for the chart itself: if the OCI Helm chart at
# ghcr.io/pocteo-platform/charts/pocteo is also private, the install would
# 401 even with --local-build. Fall back to the chart embedded in the
# cloned repo (deploy/helm/pocteo) when that's reachable.
if [[ "$AIRGAP" -eq 0 && -z "$LOCAL_CHART_PATH" && "$CHART_NAME" == oci://* ]]; then
  step "Probing whether the public Helm chart is pullable…"
  # Reuse _probe_public_images logic against the chart repo path.
  _chart_repo_path="${CHART_NAME#oci://ghcr.io/}"
  if curl -fsSL --max-time 5 \
       "https://ghcr.io/token?scope=repository%3A${_chart_repo_path}%3Apull&service=ghcr.io" \
       -o /dev/null 2>/dev/null; then
    ok "public chart $CHART_NAME reachable"
  else
    if [[ -n "$REPO_ROOT" && -d "$REPO_ROOT/deploy/helm/pocteo" ]]; then
      warn "OCI chart not anonymously pullable. Falling back to local chart at $REPO_ROOT/deploy/helm/pocteo."
      LOCAL_CHART_PATH="$REPO_ROOT/deploy/helm/pocteo"
      CHART_NAME="$LOCAL_CHART_PATH"
    else
      die "OCI chart $CHART_NAME isn't reachable AND no local chart found at \$REPO_ROOT/deploy/helm/pocteo. Pass --local-chart <path> or make the OCI package public."
    fi
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
  # The models the agent asks Ollama for must be the ones pulled below
  # ("Pulling LLM models"): a wizard choice other than the default used to
  # leave the agent requesting a model that was never pulled.
  --set-string "ollama.chatModel=${KEOS_CHAT_MODEL:-qwen2.5:3b-instruct-q4_K_M}"
  --set-string "ollama.embedModel=${KEOS_EMBED_MODEL:-nomic-embed-text}"
  --wait --timeout 10m
)
[[ -n "${KEOS_NUM_CTX:-}" ]] && HELM_ARGS+=(--set "ollama.numCtx=${KEOS_NUM_CTX}")
# OCI charts: helm pulls + installs by version. Local-chart path
# embeds the version in the .tgz filename so this is a no-op.
if [[ "$CHART_NAME" == oci://* ]]; then
  HELM_ARGS+=(--version "$CHART_VERSION")
  [[ "$HELM_PLAIN_HTTP" -eq 1 ]] && HELM_ARGS+=(--plain-http)
fi

# Every image — first-party AND stock — comes from the operator's registry.
# First-party (api/mcp/frontend) via global.imageRegistry (the chart's
# `pocteo.image` helper applies it only to firstParty=true); stock images
# need explicit per-image repo overrides because they're pulled verbatim
# from values.yaml. Paths match what `scripts/release.sh mirror` pushes.
apply_registry_overrides() {
  HELM_ARGS+=(
    --set-string "global.imageRegistry=${IMAGE_REGISTRY}/pocteo-platform"
    --set-string "caddy.image.repository=${IMAGE_REGISTRY}/library/caddy"
    --set-string "nats.image.repository=${IMAGE_REGISTRY}/library/nats"
    --set-string "ollama.image.repository=${IMAGE_REGISTRY}/ollama/ollama"
    --set-string "qdrant.image.repository=${IMAGE_REGISTRY}/qdrant/qdrant"
    --set-string "opa.image.repository=${IMAGE_REGISTRY}/openpolicyagent/opa"
    --set-string "chaos-mesh.images.registry=${IMAGE_REGISTRY}"
    --set-string "agent.bootstrapNodeLabel.image=${IMAGE_REGISTRY}/alpine/k8s:1.30.0"
    # Security subcharts (harmless when falco/tetragon disabled). Without
    # these a --security install on a registry-only fleet still dials
    # docker.io / quay.io. Keys verified against the vendored subcharts.
    --set-string "falco.image.registry=${IMAGE_REGISTRY}"
    --set-string "falco.falcoctl.image.registry=${IMAGE_REGISTRY}"
    --set-string "falco.driver.loader.initContainer.image.registry=${IMAGE_REGISTRY}"
    --set-string "falco.falcosidekick.image.registry=${IMAGE_REGISTRY}"
    --set-string "tetragon.tetragon.image.repository=${IMAGE_REGISTRY}/cilium/tetragon"
    --set-string "tetragon.tetragonOperator.image.repository=${IMAGE_REGISTRY}/cilium/tetragon-operator"
    --set-string "tetragon.export.stdout.image.repository=${IMAGE_REGISTRY}/cilium/hubble-export-stdout"
  )
}

# Operator-supplied registry (non-airgap): the standard fleet flow — a
# self-hosted registry populated by `scripts/release.sh all` serves every
# image. Same overrides as airgap; only the chart-source probing differs.
if [[ "$AIRGAP" -eq 0 && -n "$IMAGE_REGISTRY" && "$LOCAL_BUILD" -eq 0 ]]; then
  apply_registry_overrides
  ok "using fleet registry: $IMAGE_REGISTRY (all images redirected)"
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
# Airgap: identical registry plumbing — the difference is only that the
# chart must be local (validated above) and no public probes ever run.
if [[ "$AIRGAP" -eq 1 ]]; then
  apply_registry_overrides
fi
if [[ "$ENABLE_GPU" == "true" ]]; then
  HELM_ARGS+=(
    --set "ollama.gpu.enabled=true"
    --set-string "ollama.gpu.runtimeClassName=$GPU_RUNTIME_CLASS"
  )
  # Tegra (Jetson) needs the sm_87 ollama variant + the JETSON_JETPACK
  # runner override — stock arm64 ollama images enumerate the Orin iGPU
  # then silently skip it (no sm_87 CUDA kernels), and ollama doesn't
  # recognise L4T R39+ in /etc/nv_tegra_release. Detected from node info;
  # no SSH needed. See docs/JETSON_VALIDATION_AND_PORTABILITY.md §1.4d.
  IS_TEGRA=$(printf '%s' "$NODES_RAW" | python3 -c '
import sys, json
data = json.load(sys.stdin)
for n in data["items"]:
    info = (n.get("status") or {}).get("nodeInfo") or {}
    labels = (n.get("metadata") or {}).get("labels") or {}
    machine = str(labels.get("nvidia.com/gpu.machine", "")).lower()
    if "-tegra" in info.get("kernelVersion", "") or "orin" in machine or "jetson" in machine:
        print(1); sys.exit(0)
print(0)
')
  if [[ "$IS_TEGRA" == "1" ]]; then
    OLLAMA_VERSION="${KEOS_OLLAMA_VERSION:-0.30.7}"
    _jetson_ollama_tag="${OLLAMA_VERSION}-jp6"
    if [[ -n "$IMAGE_REGISTRY" ]]; then
      # Registry flow: kubelet pulls the variant from the fleet registry.
      _jetson_ollama_repo="${IMAGE_REGISTRY}/pocteo-platform/keos-ollama-jetson"
      HELM_ARGS+=(
        --set-string "ollama.image.repository=${_jetson_ollama_repo}"
        --set-string "ollama.image.tag=${_jetson_ollama_tag}"
        --set-string "ollama.env[0].name=JETSON_JETPACK"
        --set-string "ollama.env[0].value=6"
      )
    else
      # No registry (local-build path): the bare-named variant must be in
      # the node's containerd, and kubelet must NOT try to pull it. Import
      # it from docker if present (mirrors the first-party import above);
      # fail clearly if it's nowhere — it isn't built from source here.
      _jetson_ollama_repo="keos-ollama-jetson"
      if sudo k3s ctr -n=k8s.io images ls -q 2>/dev/null | grep -q "${_jetson_ollama_repo}:${_jetson_ollama_tag}"; then
        ok "ollama variant already in containerd: ${_jetson_ollama_repo}:${_jetson_ollama_tag}"
      elif command -v docker >/dev/null 2>&1 && docker image inspect "${_jetson_ollama_repo}:${_jetson_ollama_tag}" >/dev/null 2>&1; then
        step "Importing ${_jetson_ollama_repo}:${_jetson_ollama_tag} into containerd…"
        docker save "${_jetson_ollama_repo}:${_jetson_ollama_tag}" -o "/tmp/${_jetson_ollama_repo}-${_jetson_ollama_tag}.tar"
        if command -v k3s >/dev/null 2>&1; then
          sudo k3s ctr -n=k8s.io image import "/tmp/${_jetson_ollama_repo}-${_jetson_ollama_tag}.tar" >/dev/null
        else
          sudo ctr -n=k8s.io image import "/tmp/${_jetson_ollama_repo}-${_jetson_ollama_tag}.tar" >/dev/null
        fi
        rm -f "/tmp/${_jetson_ollama_repo}-${_jetson_ollama_tag}.tar"
        ok "imported ${_jetson_ollama_repo}:${_jetson_ollama_tag}"
      else
        die "Tegra GPU needs ${_jetson_ollama_repo}:${_jetson_ollama_tag}, not found in containerd or docker.
   Build it:  docker build --build-arg OLLAMA_VERSION=${OLLAMA_VERSION} -t ${_jetson_ollama_repo}:${_jetson_ollama_tag} $REPO_ROOT/deploy/ollama-jetson
   or pass --image-registry <host:port> (populate it with scripts/release.sh), or --no-gpu for CPU."
      fi
      HELM_ARGS+=(
        --set-string "ollama.image.repository=${_jetson_ollama_repo}"
        --set-string "ollama.image.tag=${_jetson_ollama_tag}"
        --set-string "ollama.image.pullPolicy=Never"
        --set-string "ollama.env[0].name=JETSON_JETPACK"
        --set-string "ollama.env[0].value=6"
      )
    fi
    ok "Tegra/Jetson GPU detected — ollama image ${_jetson_ollama_repo}:${_jetson_ollama_tag} + JETSON_JETPACK=6"
  fi
fi
# Security domain (Phase 21): a CAPABILITY LADDER, not an unconditional
# install. The kernel decides what can actually run — forcing modern_ebpf
# + tetragon on a BTF-less kernel just crash-loops the DaemonSets and turns
# the whole install into a 10-minute helm timeout (the Jetson failure mode).
# Preflight already probed BTF and labelled nodes keos.pocteo/btf-kernel;
# we re-derive it here so the installer stands alone too.
if [[ "$ENABLE_SECURITY" -eq 1 ]]; then
  # Does the TARGET node have BTF? Prefer the preflight label; fall back to
  # a local check when we're installing from the node itself.
  _btf_label="$(kubectl get node "$NODE" -o jsonpath='{.metadata.labels.keos\.pocteo/btf-kernel}' 2>/dev/null || true)"
  if [[ -z "$_btf_label" && -e /sys/kernel/btf/vmlinux && "$(hostname 2>/dev/null)" == "$NODE" ]]; then
    _btf_label="true"
  fi
  _krel="$(uname -r 2>/dev/null || echo unknown)"
  _is_wsl=0
  grep -qiE "microsoft|wsl" /proc/version 2>/dev/null && _is_wsl=1
  [[ "$_krel" == *microsoft* ]] && _is_wsl=1

  # Common falco→agent delivery wiring (all tiers that run falco): generate
  # the webhook token, store it in the auth secret, and point falcosidekick
  # at the agent WITH the Authorization header the handler requires.
  _wire_falco_webhook() {
    FALCO_WEBHOOK_TOKEN=$(openssl rand -base64 32 | tr -d '=' | head -c 32)
    HELM_ARGS+=(
      --set-string "auth.falcoWebhookToken=${FALCO_WEBHOOK_TOKEN}"
      --set "falco.falcosidekick.enabled=true"
      --set-string "falco.falcosidekick.config.webhook.address=http://api:8000/api/webhooks/falco"
      --set-string "falco.falcosidekick.config.webhook.customHeaders=Authorization:Bearer ${FALCO_WEBHOOK_TOKEN}"
    )
  }

  if [[ "$_is_wsl" -eq 1 ]]; then
    SECURITY_TIER="off"
    warn "WSL2 kernel ($_krel) — eBPF sensors cannot run here; installing sensors-off."
    note "  The security domain still works on synthetic events (keosctl run lib-*)."
    HELM_ARGS+=(--set "falco.enabled=false" --set "tetragon.enabled=false")
  elif [[ "$_btf_label" == "true" ]]; then
    SECURITY_TIER="modern_ebpf"
    HELM_ARGS+=(
      --set "falco.enabled=true"
      --set-string "falco.driver.kind=modern_ebpf"
      --set "tetragon.enabled=true"
      --set "tetragon.tetragon.hostProcPath=/proc"
    )
    _wire_falco_webhook
    ok "security tier: modern_ebpf — Falco (CO-RE) + Tetragon (BTF kernel)"
  elif [[ "$_btf_label" == "false" ]]; then
    # No BTF: Falco legacy probe from a PREBUILT image (the in-cluster
    # loader can't compile it — glibc wall). Tetragon can't run at all.
    _probe_img=""
    if [[ -n "$IMAGE_REGISTRY" ]]; then
      _node_krel="$(kubectl get node "$NODE" -o jsonpath='{.status.nodeInfo.kernelVersion}' 2>/dev/null)"
      _probe_img="${IMAGE_REGISTRY}/pocteo-platform/falco-probe-${_node_krel}:${KEOS_FALCO_DRIVER_TAG:-8.0.0-driver}"
    fi
    if [[ -n "$_probe_img" ]]; then
      SECURITY_TIER="ebpf-prebuilt"
      HELM_ARGS+=(
        --set "falco.enabled=true"
        --set-string "falco.driver.kind=ebpf"
        --set "falco.driver.loader.enabled=false"
        --set "tetragon.enabled=false"
        --set-string "falco.extra.initContainers[0].name=keos-prebuilt-probe"
        --set-string "falco.extra.initContainers[0].image=${_probe_img}"
        --set-string "falco.extra.initContainers[0].command[0]=sh"
        --set-string "falco.extra.initContainers[0].command[1]=-c"
        --set-string "falco.extra.initContainers[0].command[2]=cp /falco-bpf.o /root/.falco/falco-bpf.o"
        --set-string "falco.extra.initContainers[0].volumeMounts[0].name=root-falco-fs"
        --set-string "falco.extra.initContainers[0].volumeMounts[0].mountPath=/root/.falco"
        --set-string "falco.driver.ebpf.path=/root/.falco/falco-bpf.o"
      )
      _wire_falco_webhook
      ok "security tier: ebpf-prebuilt — Falco legacy probe from ${_probe_img}; Tetragon off (no BTF)"
    else
      SECURITY_TIER="off"
      warn "no BTF on $NODE and no prebuilt Falco probe available — installing sensors-off."
      note "  Build one: sudo REGISTRY=<reg> keosctl falco-probe build (on $NODE), then re-run with --image-registry <reg>."
      note "  Synthetic-event security flow still works meanwhile."
      HELM_ARGS+=(--set "falco.enabled=false" --set "tetragon.enabled=false")
    fi
  else
    SECURITY_TIER="off"
    warn "BTF state for $NODE unknown (preflight skipped?) — installing sensors-off to avoid a crash-loop."
    note "  Run 'keosctl preflight' to classify the node, or --no-security to silence this."
    HELM_ARGS+=(--set "falco.enabled=false" --set "tetragon.enabled=false")
  fi
  HELM_ARGS+=(--set-string "keosSecurity.tier=${SECURITY_TIER}")
else
  note "security sensors (Falco + Tetragon) skipped (--no-security). The"
  note "  security domain still works on synthetic events, but live eBPF"
  note "  threat detection is off."
  HELM_ARGS+=(--set-string "keosSecurity.tier=off")
fi
helm "${HELM_ARGS[@]}"
ok "Helm release '$RELEASE' deployed"

# ─── Model pull ────────────────────────────────────────────────────────────

if [[ "$SKIP_MODEL_PULL" -eq 0 ]]; then
  step "Pulling LLM models (slow — ~5-10 min on first install)…"
  # Wait up to 5 min for the ollama pod to be Ready before we exec into it.
  # helm install --wait returns when Deployments/StatefulSets are ready,
  # but the StatefulSet's individual pod can still be in Pending mid-PVC-bind.
  # Selector uses component=ollama (the pods carry name=pocteo + component=<X>,
  # NOT name=ollama — that was a previous bug that silently skipped the pull).
  if ! kubectl wait -n "$NAMESPACE" --for=condition=Ready pod \
        -l app.kubernetes.io/component=ollama --timeout=5m 2>&1 | tail -3; then
    warn "ollama pod not Ready within 5 min; re-run with --skip-models or rerun manually:"
    warn "  kubectl exec -n $NAMESPACE ollama-0 -- ollama pull qwen2.5:3b-instruct-q4_K_M"
  else
    OLLAMA_POD=$(kubectl get pod -n "$NAMESPACE" \
                  -l app.kubernetes.io/component=ollama \
                  -o jsonpath='{.items[0].metadata.name}')
    # Models overridable for the interactive wizard (install.sh).
    # Defaults match the Jetson Orin sweet spot (3B chat + nomic embed).
    _chat="${KEOS_CHAT_MODEL:-qwen2.5:3b-instruct-q4_K_M}"
    _embed="${KEOS_EMBED_MODEL:-nomic-embed-text}"
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- ollama pull "$_chat"
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- ollama pull "$_embed"
    ok "models cached on $NODE"
  fi
fi

# ─── Runbook seed ──────────────────────────────────────────────────────────

# ─── Demo workloads ───────────────────────────────────────────────────────
# The use-case library (security + SRE scenarios) targets app=nginx-demo in
# the `workloads` namespace. Without it, every containment/heal action fails
# with "namespace not found" because there's nothing to act on. Seed the demo
# namespaces + nginx-demo by default so the use cases work out of the box on a
# fresh install. Opt out with --no-demo-workloads on a real edge cluster that
# already has its own workloads to protect.
if [[ "$SEED_DEMO_WORKLOADS" -eq 1 ]]; then
  _manifests_dir=""
  for _d in "$REPO_ROOT/manifests" "$(dirname "$(cd "$(dirname "$0")" && pwd)")/manifests"; do
    if [[ -f "$_d/demo-workloads.yaml" ]]; then _manifests_dir="$_d"; break; fi
  done
  if [[ -n "$_manifests_dir" ]]; then
    step "Seeding demo workloads (namespaces + nginx-demo)…"
    kubectl apply -f "$_manifests_dir/namespaces.yaml" >/dev/null 2>&1 \
      && kubectl apply -f "$_manifests_dir/demo-workloads.yaml" >/dev/null 2>&1 \
      && ok "demo workloads applied — use cases have a live target (app=nginx-demo/workloads)" \
      || warn "demo-workload apply returned non-zero; run 'kubectl apply -f $_manifests_dir/' manually"
  else
    warn "demo workloads manifests not found; skipping (use cases will have no target until you deploy one)"
  fi
fi

step "Seeding the runbook corpus into Qdrant…"
# Same shape as above: wait Ready before exec, then seed.
if ! kubectl wait -n "$NAMESPACE" --for=condition=Ready pod \
      -l app.kubernetes.io/component=api --timeout=2m 2>&1 | tail -3; then
  warn "api pod not Ready within 2 min; you'll need to seed manually later"
else
  API_POD=$(kubectl get pod -n "$NAMESPACE" \
              -l app.kubernetes.io/component=api \
              -o jsonpath='{.items[0].metadata.name}')
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python -m agent.seed_rag 2>&1 | tail -3 || \
    warn "seed_rag returned non-zero; the agent still works, RAG just won't enrich diagnose"
  ok "runbooks indexed"
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
