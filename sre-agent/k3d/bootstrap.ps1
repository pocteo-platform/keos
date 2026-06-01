# PowerShell mirror of bootstrap.sh — kept in lockstep so neither host needs the other.

[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$Manifests = Join-Path $RepoRoot 'manifests'

if (-not $env:KUBECONFIG) {
    $env:KUBECONFIG = Join-Path $RepoRoot '.kube\config'
}

function Step($msg) { Write-Host "[bootstrap] $msg" -ForegroundColor Cyan }
function Need($code, $what) { if ($code -ne 0) { throw "$what failed (exit $code)" } }

Step 'applying namespaces...'
kubectl apply -f (Join-Path $Manifests 'namespaces.yaml')
Need $LASTEXITCODE 'kubectl apply namespaces'

Step 'applying AgentMode CRD...'
kubectl apply -f (Join-Path $Manifests 'agent-mode-crd.yaml')
Need $LASTEXITCODE 'kubectl apply CRD'
kubectl wait --for=condition=Established --timeout=30s crd/agentmodes.security.poc
Need $LASTEXITCODE 'CRD wait'

Step 'applying RBAC (ServiceAccount + Role + Bindings)...'
kubectl apply -f (Join-Path $Manifests 'agent-rbac.yaml')
Need $LASTEXITCODE 'kubectl apply RBAC'

# ---- Chaos Mesh (Phase 9.3) ----
$InstallChaos = if ($env:INSTALL_CHAOS_MESH) { $env:INSTALL_CHAOS_MESH } else { '1' }
$HasHelm = [bool](Get-Command helm -ErrorAction SilentlyContinue)
if ($InstallChaos -eq '1' -and $HasHelm) {
    Step 'adding chaos-mesh helm repo...'
    helm repo add chaos-mesh https://charts.chaos-mesh.org 2>$null | Out-Null
    helm repo update chaos-mesh 2>$null | Out-Null

    Step 'installing chaos-mesh into ns/chaos-mesh...'
    $ChaosRuntime = if ($env:CHAOS_RUNTIME) { $env:CHAOS_RUNTIME } else { 'containerd' }
    $ChaosSocket  = if ($env:CHAOS_SOCKET)  { $env:CHAOS_SOCKET }  else { '/run/k3s/containerd/containerd.sock' }
    helm upgrade --install chaos-mesh chaos-mesh/chaos-mesh `
        --namespace chaos-mesh --create-namespace `
        --version 2.7.2 `
        --set chaosDaemon.runtime=$ChaosRuntime `
        --set chaosDaemon.socketPath=$ChaosSocket `
        --wait --timeout 120s
    if ($LASTEXITCODE -ne 0) {
        Write-Warning '[bootstrap] chaos-mesh install failed (non-fatal; continuing)'
    }

    Step 'applying chaos-rbac (agent read-only on chaos-mesh CRs)...'
    kubectl apply -f (Join-Path $Manifests 'chaos-rbac.yaml') 2>$null | Out-Null
} else {
    Step "skipping chaos-mesh install (INSTALL_CHAOS_MESH=$InstallChaos, helm available=$HasHelm)"
}

Step 'applying default AgentMode (observe-only)...'
kubectl apply -f (Join-Path $Manifests 'agentmode-default.yaml')
Need $LASTEXITCODE 'kubectl apply AgentMode CR'

Step 'applying demo workloads...'
kubectl apply -f (Join-Path $Manifests 'demo-workloads.yaml')
Need $LASTEXITCODE 'kubectl apply workloads'

Step 'waiting for nginx-demo to become Available (busybox-bad will CrashLoop on purpose)...'
kubectl -n workloads rollout status deployment/nginx-demo --timeout=120s
Need $LASTEXITCODE 'nginx-demo rollout'

Step 'cluster state:'
kubectl get pods -n workloads -o wide
kubectl get agentmode default

Step 'done.'
