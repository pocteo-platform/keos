#!/usr/bin/env bash
# Episode 5 — Help tour: the banner + per-command help.
source "$(dirname "$0")/cast-lib.sh"

intro "Episode 5 · Help tour" "keosctl help · per-command --help"

say_comment "40 commands, one tool — the full menu"
play_cmd "keosctl help | head -60"

say_comment "Every command has focused help with examples"
play_cmd "keosctl mode --help"

play_cmd "keosctl backup -h"

outro
