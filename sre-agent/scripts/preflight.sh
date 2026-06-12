#!/usr/bin/env bash
# Keos preflight — shared probe library + standalone checker.
#
# Catches environment problems in SECONDS with actionable remedies,
# instead of letting them surface as an opaque 10-minute `helm --wait`
# timeout (how every problem presented during the Jetson validation).
#
# Two consumption modes:
#   1. SOURCED by scripts/install-edge.sh / install.sh — exposes silent
#      pf_* functions that set globals (DISTRO, NODE, CONTAINERD_SOCKET,
#      GPU_NODE, IS_TEGRA, …); the caller does its own messaging.
#   2. EXECUTED standalone (also via `keosctl preflight`):
#        scripts/preflight.sh [--json] [--gpu] [--local-build]
#                             [--image-registry <host:port>] [--insecure]
#                             [--model <ollama-model>] [--node <name>]
#                             [--containerd-socket <path>] [--no-btf-label]
#      Prints a check table (or JSON) and exits with the failure count.
#
# Checks: binaries · cluster+distro/socket · node-arch vs registry ·
# memory vs model · GPU allocatable (Tegra-aware) · kernel BTF (labels
# nodes keos.pocteo/btf-kernel=true|false — the contract consumed by the
# security ladder and the tetragon nodeSelector) · WSL2 · registry
# reachability.

# ─── Probe functions (silent; set globals; return 0/1) ─────────────────────

pf_check_binaries() {
  # Args: [--need-helm] [--need-docker]. Sets PF_MISSING_BINARIES.
  local need_helm=0 need_docker=0 b
  for a in "$@"; do
    case "$a" in --need-helm) need_helm=1 ;; --need-docker) need_docker=1 ;; esac
  done
  PF_MISSING_BINARIES=()
  for b in kubectl python3; do command -v "$b" >/dev/null || PF_MISSING_BINARIES+=("$b"); done
  [[ "$need_helm" -eq 1 ]] && { command -v helm >/dev/null || PF_MISSING_BINARIES+=(helm); command -v openssl >/dev/null || PF_MISSING_BINARIES+=(openssl); }
  [[ "$need_docker" -eq 1 ]] && { command -v docker >/dev/null || PF_MISSING_BINARIES+=(docker); }
  [[ ${#PF_MISSING_BINARIES[@]} -eq 0 ]]
}

pf_cluster_reachable() {
  kubectl version --request-timeout=5s >/dev/null 2>&1
}

pf_probe_cluster() {
  # Sets: K8S_VERSION NODES_RAW NODE_COUNT DISTRO NODE_FINGERPRINT
  K8S_VERSION=$(kubectl version -o json 2>/dev/null \
    | python3 -c 'import sys,json; print(json.load(sys.stdin)["serverVersion"]["gitVersion"])' 2>/dev/null \
    || echo "unknown")
  NODES_RAW=$(kubectl get nodes -o json)
  NODE_COUNT=$(echo "$NODES_RAW" | python3 -c 'import sys,json; print(len(json.load(sys.stdin)["items"]))')
  DISTRO="kubeadm"
  [[ "$K8S_VERSION" == *"k3s"*  ]] && DISTRO="k3s"
  [[ "$K8S_VERSION" == *"rke2"* ]] && DISTRO="rke2"
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
}

pf_containerd_socket() {
  # Sets CONTAINERD_SOCKET from DISTRO unless CONTAINERD_SOCKET_OVERRIDE set.
  if [[ -n "${CONTAINERD_SOCKET_OVERRIDE:-}" ]]; then
    CONTAINERD_SOCKET="$CONTAINERD_SOCKET_OVERRIDE"
    return
  fi
  case "${DISTRO:-}" in
    k3s|kairos|rke2) CONTAINERD_SOCKET="/run/k3s/containerd/containerd.sock" ;;
    *)               CONTAINERD_SOCKET="/run/containerd/containerd.sock" ;;
  esac
}

pf_probe_gpu() {
  # Sets: GPU_NODE (first node advertising nvidia.com/gpu), IS_TEGRA,
  # TEGRA_NODE (first Tegra node, GPU advertised or not).
  GPU_NODE=$(printf '%s' "${NODES_RAW:?pf_probe_cluster first}" | python3 -c '
import sys, json
data = json.load(sys.stdin)
for n in data["items"]:
    cap = (n.get("status") or {}).get("capacity") or {}
    if int(cap.get("nvidia.com/gpu", 0) or 0) > 0:
        print(n["metadata"]["name"]); sys.exit(0)
')
  TEGRA_NODE=$(printf '%s' "$NODES_RAW" | python3 -c '
import sys, json
data = json.load(sys.stdin)
for n in data["items"]:
    info = (n.get("status") or {}).get("nodeInfo") or {}
    labels = (n.get("metadata") or {}).get("labels") or {}
    machine = str(labels.get("nvidia.com/gpu.machine", "")).lower()
    if "-tegra" in info.get("kernelVersion", "") or "orin" in machine or "jetson" in machine:
        print(n["metadata"]["name"]); sys.exit(0)
')
  IS_TEGRA=$([[ -n "$TEGRA_NODE" ]] && echo 1 || echo 0)
}

pf_node_allocatable_mem_gib() {
  # $1 = node name → prints allocatable memory in GiB (integer).
  kubectl get node "$1" -o jsonpath='{.status.allocatable.memory}' 2>/dev/null | python3 -c '
import sys
raw = sys.stdin.read().strip()
mult = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "K": 10**3, "M": 10**6, "G": 10**9}
for suf, m in mult.items():
    if raw.endswith(suf):
        print(int(raw[: -len(suf)]) * m // 2**30); break
else:
    print(int(raw or 0) // 2**30)
'
}

pf_node_btf() {
  # $1 = node name → echoes true|false|unknown. Local fast-path when we
  # ARE that node (single-board edge installs); otherwise a short probe
  # pod pinned to the node with /sys/kernel/btf host-mounted.
  local node="$1" img="${PF_PROBE_IMAGE:-alpine/k8s:1.30.0}" out
  local local_kernel node_kernel
  local_kernel="$(uname -r 2>/dev/null || true)"
  node_kernel="$(kubectl get node "$node" -o jsonpath='{.status.nodeInfo.kernelVersion}' 2>/dev/null)"
  if [[ -n "$local_kernel" && "$local_kernel" == "$node_kernel" && "$(hostname 2>/dev/null)" == "$node" ]]; then
    [[ -f /sys/kernel/btf/vmlinux ]] && echo true || echo false
    return
  fi
  out=$(kubectl run "keos-btf-probe-$RANDOM" --rm -i --restart=Never --quiet \
    --image="$img" --overrides "$(cat <<EOF
{"spec":{"nodeName":"$node","containers":[{"name":"probe","image":"$img",
"command":["sh","-c","test -f /host-btf/vmlinux && echo true || echo false"],
"volumeMounts":[{"name":"btf","mountPath":"/host-btf","readOnly":true}]}],
"volumes":[{"name":"btf","hostPath":{"path":"/sys/kernel/btf","type":""}}],
"tolerations":[{"operator":"Exists"}]}}
EOF
)" --timeout=60s 2>/dev/null | grep -E '^(true|false)$' | head -1)
  echo "${out:-unknown}"
}

pf_label_btf_nodes() {
  # Probes every node and writes keos.pocteo/btf-kernel=true|false labels
  # (the contract the security ladder + tetragon nodeSelector consume).
  # Sets PF_BTF_SUMMARY ("node=true node2=false…") and PF_BTF_ANY_TRUE.
  PF_BTF_SUMMARY=""; PF_BTF_ANY_TRUE=0
  local n v
  for n in $(printf '%s' "${NODES_RAW:?}" | python3 -c \
      'import sys,json; [print(i["metadata"]["name"]) for i in json.load(sys.stdin)["items"]]'); do
    v="$(pf_node_btf "$n")"
    PF_BTF_SUMMARY+="${n}=${v} "
    [[ "$v" == "true" ]] && PF_BTF_ANY_TRUE=1
    if [[ "$v" != "unknown" && "${PF_NO_BTF_LABEL:-0}" -ne 1 ]]; then
      kubectl label node "$n" "keos.pocteo/btf-kernel=$v" --overwrite >/dev/null 2>&1 || true
    fi
  done
}

pf_is_wsl2() {
  grep -qiE "microsoft|wsl" /proc/version 2>/dev/null || [[ "$(uname -r 2>/dev/null)" == *microsoft* ]]
}

pf_registry_reachable() {
  # $1 = host:port → 0 when /v2/ answers over https or http.
  local reg="$1"
  curl -fsk --max-time 5 "https://${reg}/v2/" -o /dev/null 2>/dev/null && return 0
  curl -fs  --max-time 5 "http://${reg}/v2/"  -o /dev/null 2>/dev/null
}

pf_image_has_arch() {
  # $1 = image ref, $2 = arch (e.g. arm64) → 0/1; returns 2 when no
  # inspect tool is available (caller should warn, not fail).
  local ref="$1" arch="$2" raw=""
  if command -v skopeo >/dev/null; then
    local flags=()
    [[ "${PF_INSECURE:-0}" == "1" ]] && flags+=(--tls-verify=false)
    raw="$(skopeo inspect "${flags[@]}" --raw "docker://$ref" 2>/dev/null)" || return 1
  elif command -v docker >/dev/null; then
    raw="$(docker manifest inspect "$ref" 2>/dev/null)" || return 1
  else
    return 2
  fi
  python3 - "$raw" "$arch" <<'EOF'
import json, sys
d = json.loads(sys.argv[1]); want = sys.argv[2]
ms = d.get("manifests") or []
ok = any((m.get("platform") or {}).get("architecture") == want for m in ms)
sys.exit(0 if ok else 1)
EOF
}

# ─── Standalone runner ──────────────────────────────────────────────────────

_pf_main() {
  set -uo pipefail
  local JSON=0 WANT_GPU=auto LOCAL_BUILD=0 REGISTRY="" MODEL="${KEOS_CHAT_MODEL:-qwen2.5:3b-instruct-q4_K_M}"
  local NODE_OVERRIDE=""
  PF_INSECURE=0; PF_NO_BTF_LABEL=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --json)              JSON=1; shift ;;
      --gpu)               WANT_GPU=true; shift ;;
      --no-gpu)            WANT_GPU=false; shift ;;
      --local-build)       LOCAL_BUILD=1; shift ;;
      --image-registry)    REGISTRY="$2"; shift 2 ;;
      --insecure)          PF_INSECURE=1; shift ;;
      --model)             MODEL="$2"; shift 2 ;;
      --node)              NODE_OVERRIDE="$2"; shift 2 ;;
      --containerd-socket) CONTAINERD_SOCKET_OVERRIDE="$2"; shift 2 ;;
      --no-btf-label)      PF_NO_BTF_LABEL=1; shift ;;
      -h|--help)           sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "unknown flag: $1" >&2; exit 2 ;;
    esac
  done

  local IDS=() STATUSES=() DETAILS=() REMEDIES=()
  add() { IDS+=("$1"); STATUSES+=("$2"); DETAILS+=("$3"); REMEDIES+=("${4:-}"); }

  # 1 · binaries
  local need_docker=(); [[ "$LOCAL_BUILD" -eq 1 ]] && need_docker=(--need-docker)
  if pf_check_binaries --need-helm "${need_docker[@]}"; then
    add binaries ok "kubectl helm openssl python3$( [[ "$LOCAL_BUILD" -eq 1 ]] && echo ' docker') present"
  else
    add binaries fail "missing: ${PF_MISSING_BINARIES[*]}" "install them (helm: https://helm.sh/docs/intro/install)"
  fi

  # 2 · cluster + distro/socket
  if pf_cluster_reachable; then
    pf_probe_cluster; pf_containerd_socket
    add cluster ok "distro=$DISTRO k8s=$K8S_VERSION nodes=$NODE_COUNT socket=$CONTAINERD_SOCKET" ""
  else
    add cluster fail "kubectl can't reach a cluster" "export KUBECONFIG and retry once 'kubectl get nodes' works"
    _pf_emit "$JSON" IDS STATUSES DETAILS REMEDIES; return
  fi

  # Target node (explicit > GPU > first) — used by memory/GPU checks.
  pf_probe_gpu
  local NODE="$NODE_OVERRIDE"
  [[ -z "$NODE" && -n "$GPU_NODE" ]] && NODE="$GPU_NODE"
  [[ -z "$NODE" ]] && NODE=$(echo "$NODES_RAW" | python3 -c \
    'import sys,json; print(json.load(sys.stdin)["items"][0]["metadata"]["name"])')

  # 3 · node arch vs registry images
  if [[ -n "$REGISTRY" ]]; then
    local version arches a missing=""
    version="$(grep -E '^version:' "$(dirname "${BASH_SOURCE[0]}")/../deploy/helm/pocteo/Chart.yaml" 2>/dev/null | awk '{print $2}')"
    arches=$(echo "$NODES_RAW" | python3 -c \
      'import sys,json; print(" ".join(sorted({i["status"]["nodeInfo"]["architecture"] for i in json.load(sys.stdin)["items"]})))')
    for a in $arches; do
      pf_image_has_arch "${REGISTRY}/pocteo-platform/keos-api:${version:-1.1.0}" "$a"
      case $? in
        1) missing+="$a " ;;
        2) add arch warn "no skopeo/docker on this machine — cannot verify image arches ($arches)" \
               "install skopeo, or run scripts/audit-images.sh elsewhere"; missing="__skip__"; break ;;
      esac
    done
    if [[ "$missing" == "" ]]; then
      add arch ok "keos-api:${version:-1.1.0} in $REGISTRY covers node arch(es): $arches"
    elif [[ "$missing" != "__skip__" ]]; then
      add arch fail "registry image missing arch(es): $missing(nodes need: $arches)" \
          "run: REGISTRY=$REGISTRY keosctl release build"
    fi
  fi

  # 4 · memory vs model. Thresholds are on ALLOCATABLE (kubelet reserves
  # headroom off the physical total: an 8GB board reports ~7Gi).
  local mem need=7
  mem=$(pf_node_allocatable_mem_gib "$NODE")
  [[ "$MODEL" == *7b* || "$MODEL" == *8b* ]] && need=14
  if [[ "${mem:-0}" -ge "$need" ]]; then
    local hint=""
    if [[ "$mem" -le 9 ]]; then
      hint=" — 8GiB-class node: add swap (k3s tolerates it) + OLLAMA_MAX_LOADED_MODELS=1; close desktop apps before model load"
      add memory warn "node $NODE: ${mem}Gi allocatable for $MODEL$hint" \
          "sudo fallocate -l 6G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile (persist in /etc/fstab)"
    else
      add memory ok "node $NODE: ${mem}Gi allocatable for $MODEL"
    fi
  else
    local remedy="pick a smaller model (qwen2.5:3b needs ~7Gi allocatable)"
    [[ "$DISTRO" == "talos" ]] && remedy="$remedy — Talos has no swap; this is a hard limit"
    add memory fail "node $NODE: ${mem:-?}Gi allocatable < ${need}Gi required for $MODEL" "$remedy"
  fi

  # 5 · GPU
  if [[ "$WANT_GPU" != "false" ]]; then
    if [[ -n "$GPU_NODE" ]]; then
      add gpu ok "nvidia.com/gpu allocatable on $GPU_NODE$( [[ "$IS_TEGRA" == 1 ]] && echo ' (Tegra/Jetson)')"
    elif [[ "$WANT_GPU" == "true" ]]; then
      local remedy="install NVIDIA container toolkit + device plugin"
      [[ "$IS_TEGRA" == 1 ]] && remedy="run 'keosctl gpu-setup' on $TEGRA_NODE (Jetson: CDI mode + tegra alias needed)"
      [[ "$DISTRO" == "talos" ]] && remedy="add the NVIDIA system extensions to the Talos machine config"
      add gpu fail "--gpu requested but no node advertises nvidia.com/gpu" "$remedy"
    else
      add gpu ok "no GPU advertised — CPU mode (auto)"
    fi
  fi

  # 6 · kernel BTF → security tier
  pf_label_btf_nodes
  if [[ "$PF_BTF_ANY_TRUE" == 1 ]]; then
    add btf ok "BTF kernels: ${PF_BTF_SUMMARY% } → security tier: modern_ebpf + tetragon"
  elif [[ "$PF_BTF_SUMMARY" == *false* ]]; then
    add btf warn "no BTF kernel (${PF_BTF_SUMMARY% }) → tier: legacy ebpf w/ prebuilt probe, tetragon off" \
        "build the probe once: keosctl falco-probe build (see docs/INSTALLATION.md)"
  else
    add btf warn "BTF state unknown (probe pod failed?) → installer will default to sensors-off" ""
  fi

  # 7 · WSL2
  if pf_is_wsl2; then
    add wsl2 warn "WSL2 kernel detected — Falco/Tetragon cannot run; use --no-security" ""
  else
    add wsl2 ok "not WSL2"
  fi

  # 8 · registry reachability (workstation side; node side is proven by
  # the BTF probe pod above actually pulling its image)
  if [[ -n "$REGISTRY" ]]; then
    if pf_registry_reachable "$REGISTRY"; then
      add registry ok "$REGISTRY/v2/ answers"
    else
      add registry fail "$REGISTRY unreachable from this machine" \
          "check DNS/firewall/TLS; device-side trust: docs/OPERATIONS.md (k3s registries.yaml)"
    fi
  fi

  _pf_emit "$JSON" IDS STATUSES DETAILS REMEDIES
}

_pf_emit() {
  local json="$1"; local -n _ids="$2" _st="$3" _de="$4" _re="$5"
  local i fails=0
  for i in "${!_st[@]}"; do [[ "${_st[$i]}" == "fail" ]] && fails=$((fails+1)); done
  if [[ "$json" -eq 1 ]]; then
    python3 - "$fails" <<EOF
import json, sys
checks = []
ids = $(printf '%s\n' "${_ids[@]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')
st  = $(printf '%s\n' "${_st[@]}"  | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')
de  = $(printf '%s\n' "${_de[@]}"  | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')
re  = $(printf '%s\n' "${_re[@]}"  | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().splitlines()))')
for t in zip(ids, st, de, re):
    checks.append({"id": t[0], "status": t[1], "detail": t[2], "remedy": t[3]})
print(json.dumps({"failures": int(sys.argv[1]), "checks": checks}, indent=2))
EOF
  else
    for i in "${!_ids[@]}"; do
      local mark="✓"; [[ "${_st[$i]}" == "warn" ]] && mark="⚠"; [[ "${_st[$i]}" == "fail" ]] && mark="✗"
      printf '[preflight] %s %-9s %s\n' "$mark" "${_ids[$i]}" "${_de[$i]}"
      [[ -n "${_re[$i]}" && "${_st[$i]}" != "ok" ]] && printf '[preflight]   ↳ %s\n' "${_re[$i]}"
    done
    echo "[preflight] done — $fails failure(s)"
  fi
  return "$fails"
}

# Execute only when run directly (not sourced).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  _pf_main "$@"
  exit $?
fi
