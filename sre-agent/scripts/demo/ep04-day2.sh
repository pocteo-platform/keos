#!/usr/bin/env bash
# Episode 4 — Day-2 ops: backup, support bundle, models, token.
source "$(dirname "$0")/cast-lib.sh"

intro "Episode 4 · Day-2 operations" "backup · diag · models · token"

say_comment "One-command state snapshot: audit chain + RAG store + helm values"
play_cmd "keosctl backup -o /tmp/demo-backup.tar.gz"

say_comment "Filing an issue? Generate a support bundle"
play_cmd "keosctl diag /tmp/demo-diag.tar.gz"

say_comment "Which LLMs are loaded on the node?"
play_cmd "keosctl models list"

say_comment "And the bearer token for the web UI"
play_cmd "keosctl token"

rm -f /tmp/demo-backup.tar.gz /tmp/demo-diag.tar.gz
outro
