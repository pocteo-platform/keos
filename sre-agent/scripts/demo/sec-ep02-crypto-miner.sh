#!/usr/bin/env bash
# Security Episode 2 — Crypto-miner: the most common post-exploitation payload.
source "$(dirname "$0")/cast-lib.sh"

intro "Security · Episode 2 · Crypto-miner" "suspicious process → isolate"

say_comment "A miner (xmrig) dialing a pool is the #1 runtime threat — it just steals CPU."
play_cmd "keosctl run lib-crypto-miner"

pause 3
say_comment "Isolating the pod severs the pool connection — mining stops the moment egress is cut."
play_cmd "keosctl events --domain security -n 3"

say_comment "The runbook the agent retrieved (security domain, RAG-filtered)"
play_cmd "keosctl runbooks show security/002-crypto-miner"

outro
