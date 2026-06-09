#!/usr/bin/env bash
# Episode 0 — The install wizard: from clean cluster to running agent.
# The wizard reads answers from stdin; we feed them on a human-paced
# timer so each screen gets its moment before the "keypress".
source "$(dirname "$0")/cast-lib.sh"

REPO="/mnt/a/argentic/DEV/self-healing-poc"

# LF-normalized copies of the wizard + its backend (repo may be CRLF).
mkdir -p /tmp/keos-demo/sre-agent/scripts
for f in install.sh install-edge.sh keosctl; do
  cp "$REPO/sre-agent/scripts/$f" "/tmp/keos-demo/sre-agent/scripts/$f"
  sed -i 's/\r$//' "/tmp/keos-demo/sre-agent/scripts/$f"
  chmod +x "/tmp/keos-demo/sre-agent/scripts/$f"
done
# The wizard's local-build path resolves REPO_ROOT from the script dir —
# it needs the build contexts. Symlink the real repo dirs in.
for d in agent mcp_server frontend deploy manifests policies; do
  ln -sfn "$REPO/sre-agent/$d" "/tmp/keos-demo/sre-agent/$d"
done

intro "Episode 0 · Install" "git clone → keosctl install → running agent"

say_comment "Three commands. The wizard handles the rest."
printf '%b' "$PROMPT"; sleep 0.5
type_out "git clone https://github.com/pocteo-platform/keos && cd keos"
printf '\n'; sleep 1.2

printf '%b' "$PROMPT"; sleep 0.4
type_out "bash sre-agent/scripts/keosctl install"
sleep 0.6
printf '\n'

# Feed the wizard its answers on a human-paced timer:
#   enter      → begin
#   enter      → after prerequisites
#   enter      → cluster pick (default = current context)
#   enter      → confirm discovered facts
#   enter      → model pick (default 3B)
#   enter      → image source (default — auto-flipped to local-build)
#   enter      → mode pick (default observe-only)
#   y          → proceed with install
{
  sleep 3;  printf '\n'   # begin
  sleep 4;  printf '\n'   # prerequisites reviewed
  sleep 6;  printf '\n'   # cluster pick
  sleep 6;  printf '\n'   # facts confirmed
  sleep 6;  printf '\n'   # model pick
  sleep 7;  printf '\n'   # image source
  sleep 5;  printf '\n'   # mode pick
  sleep 5;  printf 'y\n'  # proceed
} | bash /tmp/keos-demo/sre-agent/scripts/install.sh

pause 2
say_comment "Installed. From here on, everything is keosctl:"
play_cmd "keosctl status"

outro
