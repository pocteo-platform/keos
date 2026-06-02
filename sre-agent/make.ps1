# PowerShell wrapper for the Makefile targets.
# Compatible with Windows PowerShell 5.1 (no &&, no ternary, no ??).
# Usage:  .\make.ps1 <target>     e.g.  .\make.ps1 demo

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Target = 'help',
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$script:RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $script:RepoRoot

$Cluster        = 'poc'
$KubeconfigFile = Join-Path $script:RepoRoot '.kube\config'
$ChatModel      = 'qwen2.5:3b-instruct-q4_K_M'
$EmbedModel     = 'nomic-embed-text'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Info($msg) { Write-Host $msg -ForegroundColor Cyan }
function Ok  ($msg) { Write-Host $msg -ForegroundColor Green }

function Need-Exit($code, $msg) {
    if ($code -ne 0) { Fail "$msg (exit $code)" }
}

function Cmd-Exists($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# `pip install --user uv` puts uv.exe in %APPDATA%\Python\PythonXY\Scripts, which is
# NOT on the default user PATH on Windows. Resolve uv robustly: PATH first, then
# Python user-base Scripts dir, then fall back to `python -m uv`.
function Get-UvCommand {
    if (Cmd-Exists 'uv') { return @{ Exe = 'uv'; Args = @() } }
    $base = & python -c "import sysconfig,os; p=sysconfig.get_path('scripts', f'{os.name}_user'); print(p)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $base) {
        $candidate = Join-Path $base.Trim() 'uv.exe'
        if (Test-Path $candidate) { return @{ Exe = $candidate; Args = @() } }
    }
    return @{ Exe = 'python'; Args = @('-m', 'uv') }
}

function Invoke-Uv {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$UvArgs)
    $uv = Get-UvCommand
    & $uv.Exe @($uv.Args + $UvArgs)
}

function Invoke-Help {
    Write-Host @"
Targets:
  install       Full install: prereqs + models + compose + cluster + seed + doctor
  doctor        Diagnose every dependency and print fix commands
  lint          Run ruff + eslint
  format        Auto-format Python + frontend
  typecheck     Run mypy + tsc
  coverage      Run pytest --cov, emit term + xml reports
  up            docker compose up -d (off-cluster services)
  down          docker compose down + delete k3d cluster
  rebuild       Rebuild frontend + api + mcp-server, force-recreate containers
  rebuild-nocache  Same as rebuild but invalidates Docker layer cache first
  rebuild-full  Full down + build + up (slowest, use if rebuild misbehaves)
  pull-models   Pull Qwen2.5-3B + nomic-embed-text into Ollama
  seed          Load runbooks into Qdrant
  cluster       Create k3d cluster + apply manifests
  cluster-down  Delete the k3d cluster only
  kubeconfig    Re-export .\.kube\config from the cluster
  test-policy   Run pytest against the OPA policy via HTTP
  test          Run all pytest suites
  demo          End-to-end narrative: observe-only -> escalate, then active -> heal
  logs          Tail logs from all compose services
  ps            Show compose + cluster status
  clean         down + drop named volumes (re-download models next time)
"@
}

function Ensure-EnvLocalToken {
    # Generate a stable bearer token into .env.local on first `up` so the
    # SPA can read it via `cat .env.local`. Idempotent.
    $envLocal = Join-Path $script:RepoRoot '.env.local'
    if (-not (Test-Path $envLocal)) {
        $bytes = New-Object byte[] 24
        [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $tok = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+','-').Replace('/','_')
        Set-Content -Path $envLocal -Value "SHP_API_TOKEN=$tok" -Encoding utf8
        Ok "Generated SHP_API_TOKEN in .env.local"
    }
}

function Ensure-KubeconfigPlaceholders {
    # Docker Desktop on Windows treats missing bind-mount sources by creating
    # an EMPTY DIRECTORY at the destination inside the container. Once that
    # happens, the kubeconfig mount is permanently broken until the container
    # is recreated. The compose stack mounts .kube/config-container into
    # api, mcp-server, and k8sgpt. If we run `up` before `cluster` (which
    # generates the kubeconfig), every dependent service starts with a
    # directory at /root/.kube/config and can never reach the cluster.
    #
    # Workaround: seed an empty file at each mount source before `up`. The
    # file gets overwritten by Invoke-Kubeconfig when the cluster is ready;
    # in the meantime, Docker bind-mounts an empty file (which kubectl will
    # error on cleanly) instead of materialising a directory.
    $kubeDir = Join-Path $script:RepoRoot '.kube'
    New-Item -ItemType Directory -Force -Path $kubeDir | Out-Null
    foreach ($name in @('config', 'config-container')) {
        $p = Join-Path $kubeDir $name
        if (-not (Test-Path $p)) {
            New-Item -ItemType File -Force -Path $p | Out-Null
        }
    }
}

function Invoke-Up {
    Ensure-EnvLocalToken
    Ensure-KubeconfigPlaceholders
    docker compose up -d
    Need-Exit $LASTEXITCODE "docker compose up failed"
    Ok "Compose stack starting. Run '.\make.ps1 ps' to see health."
}

# GPU-accelerated variant — layers docker-compose.gpu.yml on top of the
# base compose file so the Ollama container claims the NVIDIA GPU.
# Smoke-tests passthrough first so failures fail fast with a clear msg.
function Invoke-UpGpu {
    Ensure-EnvLocalToken
    Ensure-KubeconfigPlaceholders
    Info "Smoke-testing GPU passthrough..."
    docker run --rm --gpus all nvidia/cuda:12.3.0-base-ubuntu22.04 nvidia-smi -L
    if ($LASTEXITCODE -ne 0) {
        Fail "GPU passthrough failed. See docs/operations/gpu-inference.md."
    }
    docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d
    Need-Exit $LASTEXITCODE "docker compose up (gpu overlay) failed"
    Ok "Compose stack starting with GPU acceleration on ollama."
    Ok "First scenario should complete in ~10s (vs ~5m on CPU)."
}

function Invoke-Down {
    docker compose down
    if (Cmd-Exists 'k3d') {
        $existing = & k3d cluster list -o json 2>$null
        if ($LASTEXITCODE -eq 0 -and $existing -match $Cluster) {
            k3d cluster delete $Cluster
        }
    }
    Ok "Stack down. Named volumes retained (use clean to drop)."
}

function Invoke-Restart { Invoke-Down; Invoke-Up }

function Invoke-Rebuild {
    # Rebuild the three locally-built services (frontend, api, mcp-server)
    # and FORCE-RECREATE the containers from the new images. Docker's
    # default `up --build` sometimes keeps the old running container if it
    # can't detect a meaningful config change — especially on Windows. We
    # use --force-recreate to guarantee fresh containers and --build to
    # rebuild the images first. Stateful services (ollama, qdrant, nats,
    # opa, caddy) keep running.
    param([switch]$NoCache, [switch]$Full)
    Ensure-EnvLocalToken
    Ensure-KubeconfigPlaceholders

    if ($Full) {
        Info "Full rebuild: down -> build -> up (slowest but bullet-proof)..."
        docker compose down
        Need-Exit $LASTEXITCODE "docker compose down failed"
        if ($NoCache) {
            docker compose build --no-cache api frontend mcp-server
        } else {
            docker compose build api frontend mcp-server
        }
        Need-Exit $LASTEXITCODE "docker compose build failed"
        docker compose up -d
        Need-Exit $LASTEXITCODE "docker compose up failed"
    } else {
        if ($NoCache) {
            Info "Rebuilding (no cache) api, frontend, mcp-server..."
            docker compose build --no-cache api frontend mcp-server
            Need-Exit $LASTEXITCODE "docker compose build failed"
        } else {
            Info "Rebuilding (cached) api, frontend, mcp-server..."
            docker compose build api frontend mcp-server
            Need-Exit $LASTEXITCODE "docker compose build failed"
        }
        Info "Recreating containers from new images..."
        docker compose up -d --force-recreate --no-deps api frontend mcp-server
        Need-Exit $LASTEXITCODE "docker compose up failed"
    }

    Ok "Rebuilt. Hard-refresh the browser (Ctrl+Shift+R) to pick up new SPA assets."
}

function Invoke-Logs {
    docker compose logs -f --tail=100
}

function Invoke-Ps {
    docker compose ps
    Write-Host '---'
    if (Cmd-Exists 'k3d') { k3d cluster list }
}

function Test-UvAvailable {
    if (Cmd-Exists 'uv') { return $true }
    # Look in the Python user-base Scripts dir (where `pip install --user uv` lands).
    # Use the Windows launcher `py -3` to dodge any activated repo-local venv that
    # itself has no pip (a common trap when uv created the venv).
    $base = & py -3 -c "import sysconfig,os; p=sysconfig.get_path('scripts', f'{os.name}_user'); print(p)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $base) {
        if (Test-Path (Join-Path $base.Trim() 'uv.exe')) { return $true }
    }
    return $false
}

function Invoke-Install {
    Info "Ensuring uv is installed..."
    if (Test-UvAvailable) {
        Ok "uv already present; skipping install."
    } else {
        # Prefer the Windows Python launcher so we never target an active venv.
        # If `py` is missing, fall back to the official uv standalone installer.
        if (Cmd-Exists 'py') {
            py -3 -m pip install --user --quiet --upgrade uv
            if ($LASTEXITCODE -ne 0) {
                Info "pip install via 'py -3' failed; trying official uv installer..."
                powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
                Need-Exit $LASTEXITCODE "uv install failed"
            }
        } else {
            Info "Using official uv installer..."
            powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
            Need-Exit $LASTEXITCODE "uv install failed"
        }
        if (-not (Test-UvAvailable)) { Fail "uv installed but not on PATH; restart PowerShell and retry." }
    }

    if (-not (Cmd-Exists 'k3d')) {
        Info "k3d missing; attempting 'winget install k3d-io.k3d'..."
        winget install --silent --accept-package-agreements --accept-source-agreements k3d-io.k3d
        if ($LASTEXITCODE -ne 0) { Fail "Install k3d manually: https://k3d.io/#installation" }
    }

    $opaPath = Join-Path $script:RepoRoot 'bin\opa.exe'
    if (-not (Test-Path $opaPath)) {
        New-Item -ItemType Directory -Force -Path (Join-Path $script:RepoRoot 'bin') | Out-Null
        Info "Downloading opa.exe..."
        Invoke-WebRequest -UseBasicParsing -Uri 'https://openpolicyagent.org/downloads/v0.69.0/opa_windows_amd64.exe' -OutFile $opaPath
    }

    Info "Syncing Python deps with uv..."
    Invoke-Uv sync
    Need-Exit $LASTEXITCODE "uv sync failed"

    # Full install path: every step needed to reach a working demo, in
    # dependency order. Each step is idempotent so re-running is cheap.
    Invoke-Up                       # 1. compose services
    Invoke-PullModels               # 2. Ollama models (~2 GB on cold cache)
    Invoke-Cluster                  # 3. k3d cluster + manifests + Chaos Mesh
    Start-Sleep -Seconds 2          # let the api container pick up new kubeconfig
    Invoke-Seed                     # 4. seed Qdrant runbooks collection
    Ok "Install complete. Running doctor to verify..."
    Invoke-Doctor
}

function Invoke-Doctor {
    # Runs the standalone diagnostic against the live API. Tells you which
    # component (Ollama, Qdrant, NATS, OPA, cluster, AgentMode CR) is
    # missing or broken AND the exact command to fix it.
    Info "Running system doctor..."
    Invoke-Uv run python -m scripts.doctor
    # Doctor exits non-zero if anything's wrong; we want to KEEP showing the
    # results to the operator instead of bailing with our generic error.
    if ($LASTEXITCODE -ne 0) {
        Info "Doctor found issues. Fix them and re-run '.\make.ps1 doctor'."
    }
}

# ------------------------------------------------------------------
# Code quality targets — mirrored from the Makefile so the same
# commands work on Windows. Wired into .github/workflows/ci.yml.
# ------------------------------------------------------------------

function Invoke-Lint {
    Info "ruff check ..."
    Invoke-Uv run ruff check .
    if ($LASTEXITCODE -ne 0) { Fail "ruff lint failed" }
    Info "frontend eslint ..."
    Push-Location (Join-Path $script:RepoRoot 'frontend')
    try {
        npm run lint
        if ($LASTEXITCODE -ne 0) { Fail "frontend lint failed" }
    } finally { Pop-Location }
}

function Invoke-Format {
    Info "ruff format + auto-fix ..."
    Invoke-Uv run ruff format .
    Invoke-Uv run ruff check --fix .
    Push-Location (Join-Path $script:RepoRoot 'frontend')
    try {
        # `--fix` exits non-zero if any unfixable issues remain; tolerate it.
        npm run lint -- --fix
    } finally { Pop-Location }
}

function Invoke-Typecheck {
    Info "mypy ..."
    Invoke-Uv run mypy agent mcp_server scripts
    if ($LASTEXITCODE -ne 0) { Fail "mypy failed" }
    Info "tsc --noEmit ..."
    Push-Location (Join-Path $script:RepoRoot 'frontend')
    try {
        npm run typecheck
        if ($LASTEXITCODE -ne 0) { Fail "tsc failed" }
    } finally { Pop-Location }
}

function Invoke-Coverage {
    Info "pytest --cov ..."
    Invoke-Uv run pytest --cov --cov-report=term-missing --cov-report=xml
    if ($LASTEXITCODE -ne 0) { Fail "coverage run failed" }
}

function Invoke-PullModels {
    python (Join-Path $script:RepoRoot 'agent\pull_models.py')
    Need-Exit $LASTEXITCODE "model pull failed"
}

function Invoke-Seed {
    Invoke-Uv run python -m agent.seed_rag
    Need-Exit $LASTEXITCODE "seed_rag failed"
}

function Invoke-Cluster {
    if (-not (Cmd-Exists 'k3d')) { Fail "k3d not on PATH. Run 'make.ps1 install' first." }
    $exists = $false
    $list = & k3d cluster list -o json 2>$null
    if ($LASTEXITCODE -eq 0 -and $list -match $Cluster) { $exists = $true }
    if (-not $exists) {
        Info "Creating k3d cluster '$Cluster'..."
        k3d cluster create --config (Join-Path $script:RepoRoot 'k3d\cluster.yaml')
        Need-Exit $LASTEXITCODE "k3d cluster create failed"
    }
    Invoke-Kubeconfig
    Info "Bootstrapping manifests..."
    & powershell -ExecutionPolicy Bypass -File (Join-Path $script:RepoRoot 'k3d\bootstrap.ps1')
    Need-Exit $LASTEXITCODE "bootstrap.ps1 failed"
}

function Invoke-ClusterDown {
    if (Cmd-Exists 'k3d') { k3d cluster delete $Cluster }
}

function Write-KubeconfigFile {
    param([string]$Path, [string]$Content)
    # On Windows, compose may have the file bind-mounted read-only into
    # k8sgpt / mcp-server / api containers, which holds an OS-level lock.
    # Set-Content then errors with UnauthorizedAccessException. Clear the
    # read-only bit, try to delete, fall back to instructing the user.
    if (Test-Path $Path) {
        try {
            (Get-Item $Path -Force).Attributes = 'Normal'
            Remove-Item -Force $Path -ErrorAction Stop
        } catch {
            Info "Could not replace $Path (likely held by a running container)."
            Info "Stopping compose services that mount it..."
            & docker compose stop k8sgpt mcp-server api 2>$null | Out-Null
            Start-Sleep -Milliseconds 500
            try {
                Remove-Item -Force $Path -ErrorAction Stop
            } catch {
                Fail "Cannot overwrite $Path. Run 'docker compose down' and retry."
            }
        }
    }
    # PS 5.1's `Set-Content -Encoding utf8` writes a UTF-8 BOM, which makes
    # kubectl's Go YAML parser fail with "mapping values are not allowed in
    # this context". Use .NET's UTF8Encoding($false) to write BOM-less UTF-8.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

function Invoke-Kubeconfig {
    New-Item -ItemType Directory -Force -Path (Join-Path $script:RepoRoot '.kube') | Out-Null
    # PS 5.1 captures native-exe stdout as a string[] (one element per line).
    # If we pass that array to .NET WriteAllText it gets joined with $OFS (a
    # single space) — collapsing the whole YAML doc to one line and breaking
    # kubectl. Force a string join with LF here so the file is real YAML.
    $rawLines = & k3d kubeconfig get $Cluster
    Need-Exit $LASTEXITCODE "k3d kubeconfig get failed"
    $raw = if ($rawLines -is [array]) { $rawLines -join "`n" } else { [string]$rawLines }
    # Host-facing: 127.0.0.1:6550 (agent + kubectl on this machine).
    $hostCfg = $raw -replace 'server:\s*https://[^\s]+', 'server: https://127.0.0.1:6550'
    Write-KubeconfigFile -Path $KubeconfigFile -Content $hostCfg
    # Container-facing: host.docker.internal:6550 (k8sgpt, mcp-server).
    $contCfg = $raw -replace 'server:\s*https://[^\s]+', 'server: https://host.docker.internal:6550'
    Write-KubeconfigFile -Path (Join-Path $script:RepoRoot '.kube\config-container') -Content $contCfg
    Ok "Wrote $KubeconfigFile and .\.kube\config-container"

    # If api/mcp-server/k8sgpt started before the kubeconfig file existed,
    # their bind mounts are stale (Docker materialised an empty directory at
    # the destination). Recreate them so they pick up the file mount.
    $running = & docker compose ps --services --status running 2>$null
    if ($LASTEXITCODE -eq 0) {
        $needsRecreate = @()
        foreach ($svc in @('api', 'mcp-server', 'k8sgpt')) {
            if ($running -contains $svc) {
                $check = & docker compose exec -T $svc sh -c "test -f /root/.kube/config -o -f /home/nonroot/.kube/config && echo OK || echo BROKEN" 2>$null
                if ($check -notmatch 'OK') { $needsRecreate += $svc }
            }
        }
        if ($needsRecreate.Count -gt 0) {
            Info ("Recreating services with stale kubeconfig mount: " + ($needsRecreate -join ', '))
            & docker compose rm -sf @needsRecreate | Out-Null
            & docker compose up -d @needsRecreate | Out-Null
            Ok "Services recreated; cluster should now be reachable from the UI."
        }
    }
}

function Invoke-TestPolicy {
    Invoke-Uv run pytest -v tests\test_policy.py
    Need-Exit $LASTEXITCODE "test-policy failed"
}

function Invoke-Test {
    Invoke-Uv run pytest -v tests
    Need-Exit $LASTEXITCODE "tests failed"
}

function Invoke-Demo {
    Invoke-Uv run python -m agent.demo
    Need-Exit $LASTEXITCODE "demo failed"
}

function Invoke-DemoUi {
    Invoke-Up
    Invoke-Uv run python -m agent.demo_ui
    Need-Exit $LASTEXITCODE "demo-ui failed"
}

function Invoke-Clean {
    Invoke-Down
    docker volume rm shp_ollama_models shp_qdrant_data shp_nats_data 2>$null | Out-Null
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $script:RepoRoot '.kube')
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $script:RepoRoot 'bin')
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $script:RepoRoot 'agent\audit.log')
    Ok "Clean. Next install will re-download models."
}

switch ($Target.ToLower()) {
    'help'         { Invoke-Help }
    'install'      { Invoke-Install }
    'up'           { Invoke-Up }
    'up-gpu'       { Invoke-UpGpu }
    'down'         { Invoke-Down }
    'restart'      { Invoke-Restart }
    'rebuild'      { Invoke-Rebuild }
    'rebuild-nocache' { Invoke-Rebuild -NoCache }
    'rebuild-full' { Invoke-Rebuild -Full }
    'doctor'       { Invoke-Doctor }
    'lint'         { Invoke-Lint }
    'format'       { Invoke-Format }
    'typecheck'    { Invoke-Typecheck }
    'coverage'     { Invoke-Coverage }
    'logs'         { Invoke-Logs }
    'ps'           { Invoke-Ps }
    'pull-models'  { Invoke-PullModels }
    'seed'         { Invoke-Seed }
    'cluster'      { Invoke-Cluster }
    'cluster-down' { Invoke-ClusterDown }
    'kubeconfig'   { Invoke-Kubeconfig }
    'test-policy'  { Invoke-TestPolicy }
    'test'         { Invoke-Test }
    'demo'         { Invoke-Demo }
    'demo-ui'      { Invoke-DemoUi }
    'clean'        { Invoke-Clean }
    default        { Fail "Unknown target '$Target'. Run '.\make.ps1 help' for the list." }
}
