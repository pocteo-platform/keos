#!/usr/bin/env bash
# Apply the PoC manifests in the order they must land:
#   1. namespaces  (everything else depends on them)
#   2. CRD          (must exist before any AgentMode CR)
#   3. RBAC
#   4. default AgentMode CR
#   5. demo workloads
#
# Idempotent — re-runnable after `make cluster` to refresh manifests in-place.
set -euo pipefail

KUBECONFIG_FILE="${KUBECONFIG:-./.kube/config}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
MANIFESTS="$REPO_ROOT/manifests"

export KUBECONFIG="$KUBECONFIG_FILE"

echo "[bootstrap] applying namespaces..."
kubectl apply -f "$MANIFESTS/namespaces.yaml"

echo "[bootstrap] applying AgentMode CRD..."
kubectl apply -f "$MANIFESTS/agent-mode-crd.yaml"
# Wait for the CRD to be established before applying the CR.
kubectl wait --for=condition=Established --timeout=30s \
  crd/agentmodes.security.poc

echo "[bootstrap] applying RBAC (ServiceAccount + Role + Bindings)..."
kubectl apply -f "$MANIFESTS/agent-rbac.yaml"

# ---- Chaos Mesh (Phase 9.3) ----
# Install Chaos Mesh into ns/chaos-mesh via Helm. The Use Case wizard
# applies ChaosExperiment CRs against this controller; the kubelet events
# the experiments produce flow to the agent via chaos_observer -> NATS.
#
# Set INSTALL_CHAOS_MESH=0 to skip (useful for offline CI).
INSTALL_CHAOS_MESH="${INSTALL_CHAOS_MESH:-1}"
if [ "$INSTALL_CHAOS_MESH" = "1" ] && command -v helm >/dev/null 2>&1; then
  echo "[bootstrap] adding chaos-mesh helm repo..."
  helm repo add chaos-mesh https://charts.chaos-mesh.org >/dev/null 2>&1 || true
  helm repo update chaos-mesh >/dev/null 2>&1 || true

  echo "[bootstrap] installing chaos-mesh into ns/chaos-mesh..."
  # Runtime + socketPath depend on the host's container runtime. k3s ships
  # containerd at /run/k3s/containerd/containerd.sock; Talos uses
  # /run/containerd/containerd.sock; Docker Desktop k3d uses the k3s path.
  CHAOS_RUNTIME="${CHAOS_RUNTIME:-containerd}"
  CHAOS_SOCKET="${CHAOS_SOCKET:-/run/k3s/containerd/containerd.sock}"
  helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh \
    --namespace chaos-mesh --create-namespace \
    --version 2.7.2 \
    --set chaosDaemon.runtime="$CHAOS_RUNTIME" \
    --set chaosDaemon.socketPath="$CHAOS_SOCKET" \
    --wait --timeout 120s || echo "[bootstrap] chaos-mesh install failed (non-fatal; continuing)"

  echo "[bootstrap] applying chaos-rbac (agent read-only on chaos-mesh CRs)..."
  kubectl apply -f "$MANIFESTS/chaos-rbac.yaml" || true
else
  echo "[bootstrap] skipping chaos-mesh install (INSTALL_CHAOS_MESH=$INSTALL_CHAOS_MESH or helm not on PATH)"
fi

echo "[bootstrap] applying default AgentMode (observe-only)..."
kubectl apply -f "$MANIFESTS/agentmode-default.yaml"

echo "[bootstrap] applying demo workloads..."
kubectl apply -f "$MANIFESTS/demo-workloads.yaml"

echo "[bootstrap] waiting for nginx-demo to become Available (busybox-bad will CrashLoop on purpose)..."
kubectl -n workloads rollout status deployment/nginx-demo --timeout=120s

echo "[bootstrap] cluster state:"
kubectl get pods -n workloads -o wide
kubectl get agentmode default

echo "[bootstrap] done."
