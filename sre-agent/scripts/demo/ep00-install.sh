#!/usr/bin/env bash
# Episode 0 — The install wizard: from clean cluster to running agent.
# The wizard reads answers from stdin; we feed them on a human-paced
# timer so each screen gets its moment before the "keypress".
source "$(dirname "$0")/cast-lib.sh"

REPO="/mnt/a/argentic/DEV/self-healing-poc"

# Real LF-normalized copy of the repo (docker build contexts can't follow
# symlinks, so this must be a true copy — exclude the heavy dirs).
mkdir -p /tmp/keos-demo/sre-agent
rsync -a --delete \
  --exclude node_modules --exclude website --exclude .venv \
  --exclude __pycache__ --exclude out \
  "$REPO/sre-agent/" /tmp/keos-demo/sre-agent/
find /tmp/keos-demo/sre-agent/scripts -maxdepth 1 -type f \
  -exec sed -i 's/\r$//' {} \; -exec chmod +x {} \;

intro "Episode 0 · The install wizard" "git clone → keosctl install · 6 steps, zero docs needed"

say_comment "Three commands. The wizard handles the rest."
printf '%b' "$PROMPT"; sleep 0.5
type_out "git clone https://github.com/pocteo-platform/keos && cd keos"
printf '\n'; sleep 1.2

printf '%b' "$PROMPT"; sleep 0.4
type_out "bash sre-agent/scripts/keosctl install"
sleep 0.6
printf '\n'

# Feed the wizard its answers on a human-paced timer. This episode is
# the WIZARD TOUR — we walk all 6 steps + the Review card, then answer
# N so the cast stays deterministic + 60 seconds. The real install run
# is Episode 1's starting state (and ~5 min of mostly helm --wait).
{
  sleep 3;  printf '\n'   # begin
  sleep 4;  printf '\n'   # prerequisites reviewed
  sleep 6;  printf '\n'   # cluster pick (default = current context)
  sleep 6;  printf '\n'   # facts confirmed
  sleep 6;  printf '\n'   # model pick (default 3B)
  sleep 7;  printf '\n'   # image source (auto-detected)
  sleep 5;  printf '\n'   # mode pick (observe-only)
  sleep 6;  printf 'N\n'  # review card — stop here for the tour
} | bash /tmp/keos-demo/sre-agent/scripts/install.sh || true

pause 1
say_comment "Answer Y instead and ~5 minutes later: 13 pods, models pulled, corpus seeded."
say_comment "Then everything is keosctl — see Episode 1."

outro
