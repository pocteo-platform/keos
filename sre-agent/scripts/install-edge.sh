#!/usr/bin/env bash
# Keos SRE Agent — one-command edge installer.
#
# Designed for the "Jetson arrives preconfigured, plug it in, run this"
# flow documented in docs/JETSON_FIRST_BOOT.md. Works on any single-node
# k3s install (Jetson, Raspberry Pi cluster head, Talos, generic
# kubeadm — anywhere kubectl + helm work).
#
# What it does:
#   1. Probe the cluster: distro, K8s version, GPU capacity, node names.
#   2. Generate bearer token + audit HMAC key (openssl-random, 32 bytes).
#   3. Detect the operator's chosen node (default: the node where this
#      script is running, fall back to the only node when there's one).
#   4. Run `helm upgrade --install` with sensible Jetson defaults
#      (GPU on, node-pinned hard, observe-only mode for safety).
#   5. Wait for the api pod to become Ready (~60-90s after pull).
#   6. Pull the LLM models via `kubectl exec ollama -- ollama pull`
#      (this is the slow step — ~5-10 min on first install).
#   7. Seed the RAG corpus from the agent/runbooks/*.md.
#   8. Print the URL + token the operator pastes into the SPA.
#
# Idempotent — re-running it does a `helm upgrade --install`, which
# is a no-op if nothing changed. If a step fails, the script prints
# the failing command + exits non-zero; nothing's left half-broken.
#
# Usage:
#   bash install-edge.sh                       # auto-detect node, observe-only
#   bash install-edge.sh --node KEOS-JETSON-2  # explicit node target
#   bash install-edge.sh --mode active         # start in active mode
#   bash install-edge.sh --no-gpu              # CPU-only (for non-GPU clusters)

set -euo pipefail

# ─── Defaults ──────────────────────────────────────────────────────────────

CHART_REPO="${KEOS_CHART_REPO:-https://pocteo-platform.github.io/keos-charts}"
CHART_NAME="${KEOS_CHART_NAME:-pocteo/keos-sre-agent}"
RELEASE="${KEOS_RELEASE:-keos}"
NAMESPACE="${KEOS_NAMESPACE:-keos}"
NODE_OVERRIDE=""
AGENT_MODE="observe-only"
ENABLE_GPU="auto"
SKIP_MODEL_PULL=0
PRINT_HELP=0

# ─── Argparse ──────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
  case "$1" in
    --node)        NODE_OVERRIDE="$2"; shift 2 ;;
    --mode)        AGENT_MODE="$2"; shift 2 ;;
    --release)     RELEASE="$2"; shift 2 ;;
    --namespace)   NAMESPACE="$2"; shift 2 ;;
    --no-gpu)      ENABLE_GPU="false"; shift ;;
    --gpu)         ENABLE_GPU="true"; shift ;;
    --skip-models) SKIP_MODEL_PULL=1; shift ;;
    -h|--help)     PRINT_HELP=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "$PRINT_HELP" -eq 1 ]]; then
  sed -n '3,29p' "$0" | sed 's/^# \?//'
  exit 0
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
command -v helm >/dev/null    || die "helm not on PATH (apt install helm OR https://helm.sh/docs/intro/install)"
command -v openssl >/dev/null || die "openssl not on PATH"

if ! kubectl version --request-timeout=5s >/dev/null 2>&1; then
  die "kubectl cluster unreachable. Run 'kubectl get nodes' to debug."
fi
ok "kubectl + helm + openssl OK"

# ─── Cluster context probe ────────────────────────────────────────────────

step "Probing cluster…"
K8S_VERSION=$(kubectl version -o json 2>/dev/null \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null \
  || echo "unknown")
NODES_RAW=$(kubectl get nodes -o json)
NODE_COUNT=$(echo "$NODES_RAW" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["items"]))')

# Distro detection. Mirrors agent/api.py's _detect_distro logic.
DISTRO="kubeadm"
if [[ "$K8S_VERSION" == *"k3s"* ]]; then DISTRO="k3s"; fi
if [[ "$K8S_VERSION" == *"rke2"* ]]; then DISTRO="rke2"; fi

# GPU detection on the chosen node.
GPU_AVAILABLE=0
GPU_NODE=$(echo "$NODES_RAW" | python3 <<'EOF'
import sys, json
data = json.load(sys.stdin)
for n in data["items"]:
    cap = (n.get("status") or {}).get("capacity") or {}
    if int(cap.get("nvidia.com/gpu", 0) or 0) > 0:
        print(n["metadata"]["name"])
        sys.exit(0)
EOF
)
if [[ -n "$GPU_NODE" ]]; then GPU_AVAILABLE=1; fi

# Node target: explicit > GPU node > first ready node.
if [[ -n "$NODE_OVERRIDE" ]]; then
  NODE="$NODE_OVERRIDE"
elif [[ -n "$GPU_NODE" ]]; then
  NODE="$GPU_NODE"
else
  NODE=$(echo "$NODES_RAW" | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["items"][0]["metadata"]["name"])')
fi

# Verify the chosen node exists + is Ready.
if ! kubectl get node "$NODE" >/dev/null 2>&1; then
  die "Node '$NODE' not found. Run 'kubectl get nodes' to see the list."
fi

# Resolve ENABLE_GPU=auto.
if [[ "$ENABLE_GPU" == "auto" ]]; then
  ENABLE_GPU=$( [[ "$GPU_AVAILABLE" -eq 1 ]] && echo "true" || echo "false" )
fi

ok "distro: $DISTRO · k8s: $K8S_VERSION · nodes: $NODE_COUNT"
ok "target node: $NODE · GPU: $([[ "$ENABLE_GPU" == "true" ]] && echo "yes (nvidia.com/gpu)" || echo "no — CPU mode")"

# ─── Token + HMAC key generation ──────────────────────────────────────────

step "Generating bearer token + audit HMAC key…"
API_TOKEN=$(openssl rand -base64 32 | tr -d '=' | head -c 32)
HMAC_KEY=$(openssl rand -base64 32)
ok "tokens ready (saved at ~/.keos-token)"
mkdir -p "$HOME"
umask 0077
printf '%s\n' "$API_TOKEN" > "$HOME/.keos-token"

# ─── Helm install ──────────────────────────────────────────────────────────

step "Adding the Keos Helm repo…"
if ! helm repo list 2>/dev/null | grep -q '^pocteo'; then
  helm repo add pocteo "$CHART_REPO" >/dev/null
fi
helm repo update >/dev/null
ok "repo synced"

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
if [[ "$ENABLE_GPU" == "true" ]]; then
  HELM_ARGS+=(
    --set "ollama.gpu.enabled=true"
    --set-string "ollama.gpu.runtimeClassName=nvidia"
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
    warn "ollama pod not found yet; skip --skip-models if it doesn't appear"
  else
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- \
      ollama pull qwen2.5:3b-instruct-q4_K_M >/dev/null
    kubectl exec -n "$NAMESPACE" "$OLLAMA_POD" -- \
      ollama pull nomic-embed-text >/dev/null
    ok "models cached on the Jetson"
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

# Caddy is exposed as a Service. Find the access URL — Ingress > NodePort > port-forward.
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
  Keos SRE Agent is up.
==================================================================${C_RST}

  Open in your browser:
    ${C_OK}${URL}${C_RST}

  Bearer token (paste it on the login screen):
    ${C_OK}${API_TOKEN}${C_RST}

  Saved to:  ~/.keos-token   (operator's local cache)

  Helm release: ${RELEASE}  ·  namespace: ${NAMESPACE}
  Pinned to node: ${NODE} (mode=hard)
  Agent mode: ${AGENT_MODE} ${C_DIM}(flip to 'active' from Settings → Agent)${C_RST}

${C_DIM}Diagnose: kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/component=api --tail=50${C_RST}
${C_DIM}Doctor:   curl -H 'Authorization: Bearer \$(cat ~/.keos-token)' ${URL}/api/health/detailed${C_RST}

EOF
