#!/usr/bin/env bash
# Episode 1 — Status & health: the 60-second cluster check-in.
source "$(dirname "$0")/cast-lib.sh"

intro "Episode 1 · Status & health" "status · doctor · config view"

say_comment "What's running, and is the agent healthy?"
play_cmd "keosctl status"

say_comment "Doctor goes deeper: per-component checks + LLM models + RAG corpus"
play_cmd "keosctl doctor"

say_comment "And the effective configuration in one card"
play_cmd "keosctl config view"

outro
