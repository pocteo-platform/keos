#!/usr/bin/env bash
# Security Episode 4 — Data exfiltration / suspicious egress.
source "$(dirname "$0")/cast-lib.sh"

intro "Security · Episode 4 · Data exfiltration" "suspicious egress → deny-egress"

say_comment "A pod dialing an unexpected external IP = exfil or C2. The right response keeps"
say_comment "the pod OBSERVABLE while cutting the leak."
play_cmd "keosctl run lib-data-exfil"

pause 3
say_comment "The agent applied a deny-EGRESS NetworkPolicy (DNS excepted) — the leak is cut,"
say_comment "ingress intact so IR can keep watching."
play_cmd "keosctl events --domain security -n 3"

say_comment "Two domains, one agent. Reliability + security incidents side by side."
play_cmd "keosctl events -n 6"

outro
