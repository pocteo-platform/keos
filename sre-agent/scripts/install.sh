#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  Keos SRE Agent — interactive installer
# ─────────────────────────────────────────────────────────────────────────────
#  A 6-step wizard that probes the cluster, lets the operator pick the LLM
#  model + agent mode + image source, then drives sre-agent/scripts/install-edge.sh
#  under the hood with the chosen flags. Designed to be the "git clone +
#  bash install.sh" entry point — zero docs to read first.
#
#  For non-interactive (CI / scripted) installs, use install-edge.sh directly.
#  This script is the "wow the operator" front-end on top of it.
#
#  Bash 4+ required (uses mapfile, arrays). Linux + macOS-with-brew-bash.
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
EDGE="$SCRIPT_DIR/install-edge.sh"

# ─── Colors + symbols ────────────────────────────────────────────────────────

if [[ -t 1 ]] && [[ "${NO_COLOR:-}" == "" ]]; then
  C_RST=$'\033[0m'
  C_BOLD=$'\033[1m'
  C_DIM=$'\033[2m'
  C_RED=$'\033[31m'
  C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'
  C_MAGENTA=$'\033[35m'
  C_CYAN=$'\033[36m'
  C_GREY=$'\033[90m'
else
  C_RST="" C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE=""
  C_MAGENTA="" C_CYAN="" C_GREY=""
fi

CHECK="✓"
CROSS="✗"
WARN="⚠"
ARROW="›"
BULLET="•"

# Wizard state — built up across steps, consumed by run_install.
declare -A ANS=(
  [KUBECONFIG]=""
  [CONTEXT]=""
  [DISTRO]=""
  [NODE]=""
  [GPU]="auto"
  [IMAGE_MODE]=""     # public | local-build | airgap
  [IMAGE_REGISTRY]=""
  [CHAT_MODEL]="qwen2.5:3b-instruct-q4_K_M"
  [EMBED_MODEL]="nomic-embed-text"
  [MODE]="observe-only"
  [NAMESPACE]="keos"
  [RELEASE]="keos"
)

# ─── UI helpers ──────────────────────────────────────────────────────────────

hr()         { printf "${C_GREY}────────────────────────────────────────────────────────────────${C_RST}\n"; }
heading()    { printf "\n${C_BOLD}${C_CYAN}%s${C_RST}\n" "$1"; hr; }
ok()         { printf "  ${C_GREEN}${CHECK}${C_RST} %-15s ${C_DIM}%s${C_RST}\n" "$1" "$2"; }
miss()       { printf "  ${C_RED}${CROSS}${C_RESET}${C_RED} %-14s${C_RST} ${C_DIM}%s${C_RST}\n" "$1" "$2"; }
warn_line()  { printf "  ${C_YELLOW}${WARN}${C_RST} %-15s ${C_DIM}%s${C_RST}\n" "$1" "$2"; }
info()       { printf "  ${C_BLUE}${BULLET}${C_RST} %-15s ${C_DIM}%s${C_RST}\n" "$1" "$2"; }
say()        { printf "  %s\n" "$1"; }
note()       { printf "  ${C_DIM}%s${C_RST}\n" "$1"; }

banner() {
  clear
  printf "\n"
  printf "${C_BOLD}${C_CYAN}"
  cat <<'EOF'
       ██╗  ██╗███████╗ ██████╗ ███████╗
       ██║ ██╔╝██╔════╝██╔═══██╗██╔════╝
       █████╔╝ █████╗  ██║   ██║███████╗
       ██╔═██╗ ██╔══╝  ██║   ██║╚════██║
       ██║  ██╗███████╗╚██████╔╝███████║
       ╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚══════╝
EOF
  printf "${C_RST}\n"
  printf "       ${C_BOLD}The Autonomous SRE Agent for Edge Kubernetes${C_RST}\n"
  printf "       ${C_DIM}by Pocteo Platform · v1.0.1${C_RST}\n\n"
  hr
  say "${C_DIM}This wizard installs Keos on a Kubernetes cluster.${C_RST}"
  say "${C_DIM}Estimated time: 5–15 min on a typical install (mostly LLM model download).${C_RST}"
  hr
}

step_header() {
  printf "\n${C_BOLD}${C_BLUE}STEP %s/6 ${C_RST}${C_BOLD}· %s${C_RST}\n" "$1" "$2"
  hr
}

# Read a single line with a default; default shown in brackets.
# Prompts go to stderr so command substitution `$(ask ...)` captures
# only the answer, not the prompt text.
ask() {
  local prompt="$1" default="${2:-}" answer
  if [[ -n "$default" ]]; then
    printf "  ${C_BOLD}%s${C_RST} ${C_DIM}[%s]${C_RST} " "$prompt" "$default" >&2
  else
    printf "  ${C_BOLD}%s${C_RST} " "$prompt" >&2
  fi
  read -r answer </dev/tty || answer=""
  echo "${answer:-$default}"
}

confirm() {
  local prompt="${1:-Continue?}" default="${2:-Y}" answer
  printf "  ${C_BOLD}%s${C_RST} ${C_DIM}[%s/%s]${C_RST} " \
    "$prompt" "$( [[ "$default" =~ ^[Yy] ]] && echo Y || echo y)" \
                "$( [[ "$default" =~ ^[Yy] ]] && echo n || echo N)" >&2
  read -r answer </dev/tty || answer=""
  answer="${answer:-$default}"
  [[ "$answer" =~ ^[Yy] ]]
}

# Numbered menu. usage: pick "Prompt" default_index "label1" "label2" ...
# Echoes the index of the chosen item (1-based) on stdout; everything else
# (the menu, the prompt, error message) goes to stderr so the caller can do
# `idx=$(pick ...)` cleanly.
pick() {
  local prompt="$1" default_idx="$2"; shift 2
  local opts=("$@")
  local i=1
  for opt in "${opts[@]}"; do
    if [[ "$i" -eq "$default_idx" ]]; then
      printf "    ${C_CYAN}${C_BOLD}%d)${C_RST} %s ${C_GREEN}${CHECK} default${C_RST}\n" "$i" "$opt" >&2
    else
      printf "    ${C_DIM}%d)${C_RST} %s\n" "$i" "$opt" >&2
    fi
    ((i++))
  done
  printf "\n" >&2
  local answer
  printf "  ${C_BOLD}%s${C_RST} ${C_DIM}[%s]${C_RST} " "$prompt" "$default_idx" >&2
  read -r answer </dev/tty || answer=""
  answer="${answer:-$default_idx}"
  if ! [[ "$answer" =~ ^[0-9]+$ ]] || (( answer < 1 || answer > ${#opts[@]} )); then
    printf "  ${C_RED}${CROSS} invalid choice — using default $default_idx${C_RST}\n" >&2
    answer="$default_idx"
  fi
  echo "$answer"
}

# Spinner around a long-running command. Captures output to log file.
spin() {
  local label="$1" log="$2"; shift 2
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  "$@" >"$log" 2>&1 &
  local pid=$!
  local i=0
  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${C_CYAN}%s${C_RST} %s" "${frames[i]}" "$label"
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.1
  done
  wait "$pid"
  local rc=$?
  if [[ $rc -eq 0 ]]; then
    printf "\r  ${C_GREEN}${CHECK}${C_RST} %s\n" "$label"
  else
    printf "\r  ${C_RED}${CROSS}${C_RST} %s ${C_DIM}(exit $rc — see $log)${C_RST}\n" "$label"
  fi
  return $rc
}

press_enter() {
  printf "\n  ${C_DIM}Press [enter] to continue…${C_RST}"
  read -r _ || true
}

# ─── Steps ───────────────────────────────────────────────────────────────────

step_preflight() {
  step_header 1 "Prerequisites"
  local missing=0
  for bin in kubectl helm docker openssl python3 curl; do
    if command -v "$bin" >/dev/null 2>&1; then
      local ver
      case "$bin" in
        kubectl)  ver=$(kubectl version --client=true 2>/dev/null | grep -o 'v[0-9]*\.[0-9]*\.[0-9]*' | head -1) ;;
        helm)     ver=$(helm version --short 2>/dev/null | head -c 20) ;;
        docker)   ver=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',') ;;
        openssl)  ver=$(openssl version 2>/dev/null | awk '{print $2}') ;;
        python3)  ver=$(python3 --version 2>/dev/null | awk '{print $2}') ;;
        curl)     ver=$(curl --version 2>/dev/null | head -1 | awk '{print $2}') ;;
        *)        ver="" ;;
      esac
      ok "$bin" "$ver"
    else
      miss "$bin" "not on PATH — install it first"
      missing=$((missing + 1))
    fi
  done
  if [[ $missing -gt 0 ]]; then
    printf "\n  ${C_RED}${CROSS} %d required tools missing.${C_RST}\n" "$missing"
    note "Install the missing binaries above, then re-run this script."
    exit 1
  fi
  printf "\n"
  note "All prerequisites detected."
  press_enter
}

step_cluster() {
  step_header 2 "Cluster"
  say "Detected kubectl contexts:"
  printf "\n"
  local current
  current="$(kubectl config current-context 2>/dev/null || echo '')"
  mapfile -t contexts < <(kubectl config get-contexts -o name 2>/dev/null)
  if [[ ${#contexts[@]} -eq 0 ]]; then
    printf "  ${C_RED}${CROSS} no kubectl contexts found${C_RST}\n"
    note "Configure a kubeconfig first:"
    note "  Talos:   talosctl kubeconfig ~/.kube/config"
    note "  k3s:     scp <user>@<host>:/etc/rancher/k3s/k3s.yaml ~/.kube/config"
    exit 1
  fi
  local default_idx=1
  local i=1
  for ctx in "${contexts[@]}"; do
    local marker="" reach="${C_DIM}?${C_RST}"
    if [[ "$ctx" == "$current" ]]; then
      marker="${C_GREEN}[current]${C_RST}"
      default_idx=$i
    fi
    # Quick reachability probe (3s timeout).
    local nodes=""
    if nodes=$(kubectl --context="$ctx" get nodes --request-timeout=3s 2>/dev/null | tail -n +2 | wc -l); then
      reach="${C_GREEN}${CHECK}${C_RST} ${C_DIM}nodes=$nodes${C_RST}"
    else
      reach="${C_RED}${CROSS} unreachable${C_RST}"
    fi
    printf "    ${C_DIM}%d)${C_RST} %-40s %s  %s\n" "$i" "$ctx" "$marker" "$reach"
    ((i++))
  done
  printf "\n"
  local picked
  picked=$(ask "Use context # (or path to kubeconfig)" "$default_idx")
  if [[ "$picked" =~ ^[0-9]+$ ]] && (( picked >= 1 && picked <= ${#contexts[@]} )); then
    ANS[CONTEXT]="${contexts[$((picked - 1))]}"
    note "Selected: ${ANS[CONTEXT]}"
  elif [[ -f "$picked" ]]; then
    ANS[KUBECONFIG]="$picked"
    note "Will use kubeconfig file: $picked"
  else
    printf "  ${C_RED}${CROSS} invalid choice${C_RST}\n"
    exit 1
  fi
}

# Run kubectl with the wizard's chosen context/kubeconfig.
kc() {
  if [[ -n "${ANS[KUBECONFIG]}" ]]; then
    kubectl --kubeconfig="${ANS[KUBECONFIG]}" "$@"
  elif [[ -n "${ANS[CONTEXT]}" ]]; then
    kubectl --context="${ANS[CONTEXT]}" "$@"
  else
    kubectl "$@"
  fi
}

step_facts() {
  step_header 3 "Discovered cluster facts"
  local k8s_ver os_image runtime nodes cpu mem
  k8s_ver="$(kc get nodes -o jsonpath='{.items[0].status.nodeInfo.kubeletVersion}' 2>/dev/null || echo 'unknown')"
  os_image="$(kc get nodes -o jsonpath='{.items[0].status.nodeInfo.osImage}' 2>/dev/null || echo 'unknown')"
  runtime="$(kc get nodes -o jsonpath='{.items[0].status.nodeInfo.containerRuntimeVersion}' 2>/dev/null || echo 'unknown')"
  nodes="$(kc get nodes 2>/dev/null | tail -n +2 | wc -l | tr -d ' ')"
  cpu="$(kc get nodes -o jsonpath='{.items[0].status.capacity.cpu}' 2>/dev/null || echo '?')"
  mem="$(kc get nodes -o jsonpath='{.items[0].status.capacity.memory}' 2>/dev/null || echo '?')"

  # Distro inference (matches install-edge.sh).
  local distro="generic"
  case "$os_image" in
    *Talos*|*talos*)  distro="talos" ;;
    *Kairos*|*kairos*) distro="kairos" ;;
    *k3s*)            distro="k3s" ;;
    *Debian*|*Ubuntu*) distro="kubeadm" ;;
  esac

  # GPU probe.
  local gpu_label="not detected — CPU mode"
  local gpu_cap
  gpu_cap="$(kc get nodes -o jsonpath='{.items[0].status.capacity.nvidia\.com/gpu}' 2>/dev/null || echo '')"
  if [[ -n "$gpu_cap" && "$gpu_cap" != "0" ]]; then
    gpu_label="${C_GREEN}NVIDIA GPU detected${C_RST} (×$gpu_cap)"
    ANS[GPU]="true"
  fi

  # Node pin — first node by default.
  ANS[NODE]="$(kc get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo '')"
  ANS[DISTRO]="$distro"

  info "Kubernetes"   "$k8s_ver  ·  $nodes node(s)"
  info "OS image"     "$os_image"
  info "Runtime"      "$runtime"
  info "Distro"       "$distro"
  info "Target node"  "${ANS[NODE]}"
  info "Capacity"     "$cpu vCPU · $mem"
  printf "  ${C_BLUE}${BULLET}${C_RST} %-15s %s\n" "GPU" "$gpu_label"
  printf "\n"
  if confirm "Continue with these defaults?" "Y"; then
    return 0
  fi
  # Operator override path.
  local override
  override="$(ask "Pin to a different node (or blank to keep current)" "${ANS[NODE]}")"
  ANS[NODE]="${override:-${ANS[NODE]}}"
}

step_model() {
  step_header 4 "LLM model"
  say "Pick the chat model sized for your hardware:"
  printf "\n"
  # The 4 supported options with size + speed estimates.
  local opts=(
    "${C_BOLD}qwen2.5:1.5b-instruct-q4_K_M${C_RST}   ${C_DIM}~0.9 GB  · ~2 s/call Jetson · low memory${C_RST}"
    "${C_BOLD}qwen2.5:3b-instruct-q4_K_M${C_RST}    ${C_DIM}~1.9 GB  · ~5 s/call Jetson · ${C_GREEN}recommended${C_RST}"
    "${C_BOLD}qwen2.5:7b-instruct-q4_K_M${C_RST}    ${C_DIM}~4.5 GB  · ~15 s/call Jetson · highest quality${C_RST}"
    "${C_BOLD}llama3.2:3b-instruct-q4_K_M${C_RST}   ${C_DIM}~2.0 GB  · ~5 s/call Jetson · alternative${C_RST}"
  )
  local choice
  choice=$(pick "Select model #" 2 "${opts[@]}")
  case "$choice" in
    1) ANS[CHAT_MODEL]="qwen2.5:1.5b-instruct-q4_K_M" ;;
    2) ANS[CHAT_MODEL]="qwen2.5:3b-instruct-q4_K_M"  ;;
    3) ANS[CHAT_MODEL]="qwen2.5:7b-instruct-q4_K_M"  ;;
    4) ANS[CHAT_MODEL]="llama3.2:3b-instruct-q4_K_M" ;;
  esac
  note "Selected: ${ANS[CHAT_MODEL]}"
  note "Embedding model: ${ANS[EMBED_MODEL]} (270 MB — same for every install)"
}

step_images() {
  step_header 5 "Image source"
  say "The Keos container images live at ghcr.io/pocteo-platform. We'll"
  say "probe whether they're anonymously pullable from this machine."
  printf "\n"
  local image_probe="?" chart_probe="?"
  if curl -fsSL --max-time 5 \
       "https://ghcr.io/token?scope=repository%3Apocteo-platform/keos-api%3Apull&service=ghcr.io" \
       -o /dev/null 2>/dev/null; then
    image_probe="${C_GREEN}${CHECK} public${C_RST}"
  else
    image_probe="${C_YELLOW}${WARN} private (403)${C_RST}"
  fi
  if curl -fsSL --max-time 5 \
       "https://ghcr.io/token?scope=repository%3Apocteo-platform/charts/pocteo%3Apull&service=ghcr.io" \
       -o /dev/null 2>/dev/null; then
    chart_probe="${C_GREEN}${CHECK} public${C_RST}"
  else
    chart_probe="${C_YELLOW}${WARN} private (403)${C_RST}"
  fi
  printf "  ${C_DIM}Images at ghcr.io/pocteo-platform/keos-*${C_RST}   %b\n" "$image_probe"
  printf "  ${C_DIM}Chart at ghcr.io/pocteo-platform/charts/pocteo${C_RST}  %b\n" "$chart_probe"
  printf "\n"
  local default_idx=1
  if [[ "$image_probe" != *public* || "$chart_probe" != *public* ]]; then
    default_idx=2
  fi
  local opts=(
    "${C_BOLD}Use public images${C_RST}                ${C_DIM}fastest — needs the GHCR packages to be public${C_RST}"
    "${C_BOLD}Build locally${C_RST}                    ${C_DIM}docker build the 3 images + import into the cluster (~5 min cold)${C_RST}"
    "${C_BOLD}Air-gapped install${C_RST}               ${C_DIM}use a mirror registry + the embedded chart${C_RST}"
  )
  local choice
  choice=$(pick "Choose image source #" "$default_idx" "${opts[@]}")
  case "$choice" in
    1) ANS[IMAGE_MODE]="public" ;;
    2) ANS[IMAGE_MODE]="local-build" ;;
    3) ANS[IMAGE_MODE]="airgap"
       ANS[IMAGE_REGISTRY]="$(ask "Mirror registry host:port" "")"
       if [[ -z "${ANS[IMAGE_REGISTRY]}" ]]; then
         printf "  ${C_RED}${CROSS} air-gap mode requires a mirror registry. Aborting.${C_RST}\n"
         exit 1
       fi
       ;;
  esac
  note "Selected: ${ANS[IMAGE_MODE]}"
}

step_mode() {
  step_header 6 "Agent default mode"
  say "Pick what the agent does when an incident lands:"
  printf "\n"
  local opts=(
    "${C_BOLD}observe-only${C_RST}     ${C_DIM}dry-run · agent diagnoses but does not mutate the cluster${C_RST} ${C_GREEN}${CHECK} safe default${C_RST}"
    "${C_BOLD}active${C_RST}           ${C_YELLOW}${WARN}${C_RST} ${C_DIM}agent mutates after policy gate — real heals${C_RST}"
    "${C_BOLD}frozen${C_RST}           ${C_DIM}no-op · for maintenance windows${C_RST}"
  )
  local choice
  choice=$(pick "Choose default mode #" 1 "${opts[@]}")
  case "$choice" in
    1) ANS[MODE]="observe-only" ;;
    2) ANS[MODE]="active"
       printf "\n  ${C_YELLOW}${WARN} You picked 'active'. The agent WILL mutate the cluster once the${C_RST}\n"
       printf "  ${C_YELLOW}OPA policy gate allows an action. Flip to observe-only from the${C_RST}\n"
       printf "  ${C_YELLOW}SPA → Control whenever you want to dry-run.${C_RST}\n"
       if ! confirm "Confirm active default?" "n"; then
         ANS[MODE]="observe-only"
         note "Reverted to observe-only."
       fi
       ;;
    3) ANS[MODE]="frozen" ;;
  esac
  note "Selected: ${ANS[MODE]}"
}

step_summary() {
  heading "Review · ready to install"
  info "Cluster"       "${ANS[CONTEXT]:-(via $-kubeconfig)} · distro=${ANS[DISTRO]}"
  info "Target node"   "${ANS[NODE]}"
  info "GPU mode"      "${ANS[GPU]}"
  info "Image source"  "${ANS[IMAGE_MODE]}${ANS[IMAGE_REGISTRY]:+ · registry=${ANS[IMAGE_REGISTRY]}}"
  info "Chat model"    "${ANS[CHAT_MODEL]}"
  info "Embed model"   "${ANS[EMBED_MODEL]}"
  info "Default mode"  "${ANS[MODE]}"
  info "Namespace"     "${ANS[NAMESPACE]}"
  info "Helm release"  "${ANS[RELEASE]}"
  printf "\n"
  if ! confirm "Proceed with install?" "Y"; then
    printf "\n  ${C_DIM}Aborted by operator. Nothing changed.${C_RST}\n"
    exit 0
  fi
}

run_install() {
  heading "Installing"
  # Build the install-edge.sh argv from the wizard's answers.
  local args=()
  args+=("--mode" "${ANS[MODE]}")
  args+=("--namespace" "${ANS[NAMESPACE]}")
  args+=("--release" "${ANS[RELEASE]}")
  if [[ "${ANS[GPU]}" == "true" ]]; then args+=("--gpu"); fi
  if [[ "${ANS[GPU]}" == "false" ]]; then args+=("--no-gpu"); fi
  if [[ -n "${ANS[KUBECONFIG]}" ]]; then args+=("--kubeconfig" "${ANS[KUBECONFIG]}"); fi
  case "${ANS[IMAGE_MODE]}" in
    local-build) args+=("--local-build") ;;
    airgap)      args+=("--airgap" "--image-registry" "${ANS[IMAGE_REGISTRY]}" \
                         "--local-chart" "$REPO_ROOT/deploy/helm/pocteo") ;;
    public)      args+=("--no-local-build-fallback") ;;
  esac

  # Models — passed via env so install-edge.sh's `ollama pull` picks them up.
  export KEOS_CHAT_MODEL="${ANS[CHAT_MODEL]}"
  export KEOS_EMBED_MODEL="${ANS[EMBED_MODEL]}"

  # Run install-edge.sh with full live output. We re-style its `[install-edge]`
  # lines so the wizard's branding stays consistent. The script is already
  # well-instrumented (preflight, probe, build, import, helm install, models,
  # seed) — we just relay its stdout.
  printf "  ${C_DIM}Driving install-edge.sh under the hood — live output below.${C_RST}\n\n"
  hr
  if bash "$EDGE" "${args[@]}"; then
    hr
    return 0
  else
    hr
    printf "\n  ${C_RED}${CROSS} install-edge.sh exited non-zero.${C_RST}\n"
    note "Re-run 'bash $EDGE ${args[*]}' to investigate."
    return 1
  fi
}

step_done() {
  printf "\n"
  printf "${C_GREEN}"
  cat <<'EOF'
       ┌─────────────────────────────────────────────────────────┐
       │                                                         │
       │      ✓  Install complete                                │
       │                                                         │
       └─────────────────────────────────────────────────────────┘
EOF
  printf "${C_RST}\n"

  local token=""
  if [[ -f "$HOME/.keos-token" ]]; then
    token="$(cat "$HOME/.keos-token")"
  fi

  heading "Access the SPA"
  say "${C_BOLD}1.${C_RST} Forward the Caddy ingress to localhost:"
  printf "\n   ${C_CYAN}kubectl port-forward -n %s svc/pocteo 8080:80${C_RST}\n\n" "${ANS[NAMESPACE]}"
  say "${C_BOLD}2.${C_RST} Open ${C_CYAN}http://localhost:8080${C_RST}"
  if [[ -n "$token" ]]; then
    say "${C_BOLD}3.${C_RST} Paste this bearer token into the login screen:"
    printf "\n   ${C_BOLD}${C_GREEN}%s${C_RST}\n\n" "$token"
    note "(token also cached at ~/.keos-token for copy/paste)"
  else
    say "${C_BOLD}3.${C_RST} Fetch the bearer token:"
    printf "\n   ${C_CYAN}kubectl -n %s get secret keos-pocteo-auth \\\\\n     -o jsonpath='{.data.SHP_API_TOKEN}' | base64 -d${C_RST}\n\n" "${ANS[NAMESPACE]}"
  fi

  heading "Next steps"
  info "Run a use case"  "Use Cases → pick lib-memory-leak → Run"
  info "Flip to active"  "Settings → Agent → Mode (or 'kubectl patch agentmode default --type=merge -p '{\"spec\":{\"mode\":\"active\"}}')"
  info "Inspect logs"    "kubectl -n ${ANS[NAMESPACE]} logs deploy/api -f"
  info "Uninstall"       "helm uninstall ${ANS[RELEASE]} -n ${ANS[NAMESPACE]}"
  printf "\n"
  note "Docs: https://pocteo-platform.github.io/keos/"
  printf "\n"
}

# ─── Main ────────────────────────────────────────────────────────────────────

main() {
  trap 'printf "\n${C_DIM}Interrupted.${C_RST}\n"; exit 130' INT
  [[ -x "$EDGE" || -r "$EDGE" ]] || { echo "install-edge.sh not found at $EDGE" >&2; exit 1; }

  banner
  if [[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]]; then
    note "--yes: skipping wizard, using all defaults."
  else
    say "Press ${C_BOLD}[enter]${C_RST} to begin, or ${C_BOLD}[Ctrl-C]${C_RST} to quit."
    read -r _ || true
  fi

  step_preflight
  step_cluster
  step_facts
  step_model
  step_images
  step_mode
  step_summary
  if run_install; then
    step_done
  else
    exit 1
  fi
}

main "$@"
