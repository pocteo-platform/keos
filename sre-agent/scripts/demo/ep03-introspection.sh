#!/usr/bin/env bash
# Episode 3 — Introspection: what can the agent do, and what does it know?
source "$(dirname "$0")/cast-lib.sh"

intro "Episode 3 · Introspection" "policy test · runbooks · images"

say_comment "Test the OPA gate: would the agent be allowed to restart a pod?"
play_cmd "keosctl policy test restart_pod high workloads active"

say_comment "Same action in kube-system? Hard deny."
play_cmd "keosctl policy test restart_pod high kube-system active"

say_comment "The runbook corpus the agent retrieves from"
play_cmd "keosctl runbooks list"

say_comment "And every image running right now"
play_cmd "keosctl images list"

outro
