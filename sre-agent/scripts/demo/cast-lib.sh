#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  cast-lib.sh — shared helpers for keosctl screencast episodes
# ─────────────────────────────────────────────────────────────────────────────
#  Sourced by every episode script. Provides:
#    say_comment "text"   — print a dimmed narration line
#    play_cmd "cmd"        — simulate human typing of a command, then run it
#    pause N               — sleep N seconds (between beats)
#    outro                 — closing card
#
#  Designed to run inside `asciinema rec -c "bash epXX.sh"` — asciinema
#  allocates a PTY so colors render. The typing jitter makes the cast feel
#  like a live human session instead of a text dump.
# ─────────────────────────────────────────────────────────────────────────────

export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"

# Self-provision a LF-normalized keosctl copy in /tmp (the repo working
# tree may be CRLF on Windows checkouts; /tmp may have been wiped by a
# WSL restart). Idempotent + cheap.
KCTL="${KCTL:-/tmp/keosctl-demo}"
if [[ ! -x "$KCTL" || "$KCTL" == "/tmp/keosctl-demo" ]]; then
  _src="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/keosctl"
  cp "$_src" /tmp/keosctl-demo
  sed -i 's/\r$//' /tmp/keosctl-demo
  chmod +x /tmp/keosctl-demo
  KCTL=/tmp/keosctl-demo
fi

C_RST=$'\033[0m'
C_DIM=$'\033[2m'
C_BOLD=$'\033[1m'
C_CYAN=$'\033[36m'
C_GREEN=$'\033[32m'
C_GREY=$'\033[90m'

PROMPT="${C_GREEN}keos-edge${C_RST}:${C_CYAN}~${C_RST}\$ "

# Simulated keystroke delay (seconds). Slight jitter per char.
type_out() {
  local s="$1" i ch
  for (( i=0; i<${#s}; i++ )); do
    ch="${s:$i:1}"
    printf '%s' "$ch"
    # 20-80ms jitter; spaces slightly slower (like a thumb hitting spacebar)
    if [[ "$ch" == " " ]]; then
      sleep 0.0$((RANDOM % 4 + 5))   # 0.05-0.08
    else
      sleep 0.0$((RANDOM % 5 + 2))   # 0.02-0.06
    fi
  done
}

say_comment() {
  printf '\n%s# %s%s\n' "$C_GREY" "$1" "$C_RST"
  sleep 1.2
}

play_cmd() {
  local cmd="$1"
  printf '%b' "$PROMPT"
  sleep 0.6
  type_out "$cmd"
  sleep 0.5
  printf '\n'
  # Run it for real — replace the script path with the short alias the
  # operator would actually use.
  eval "${cmd/keosctl/$KCTL}"
  sleep 1.8
}

pause() { sleep "${1:-1}"; }

intro() {
  local title="$1" subtitle="$2"
  clear
  printf '\n'
  printf '   %s%s%s\n' "$C_BOLD$C_CYAN" "KEOS SRE AGENT" "$C_RST"
  printf '   %s%s%s\n' "$C_BOLD" "$title" "$C_RST"
  printf '   %s%s%s\n' "$C_DIM" "$subtitle" "$C_RST"
  printf '\n'
  sleep 2.2
}

outro() {
  printf '\n%s# docs: pocteo-platform.github.io/keos  ·  keosctl help%s\n\n' "$C_GREY" "$C_RST"
  sleep 2
}
