#!/usr/bin/env bash
# Security Episode 3 — Privilege escalation / sensitive mount (container escape).
source "$(dirname "$0")/cast-lib.sh"

intro "Security · Episode 3 · Privilege escalation" "sensitive mount → contain + flag node"

say_comment "Mounting the container-runtime socket is a direct host-escape primitive."
play_cmd "keosctl run lib-priv-esc"

pause 3
say_comment "The agent contains the pod AND flags the NODE as potentially compromised —"
say_comment "an escape attempt puts the host in scope, not just the pod."
play_cmd "keosctl events --domain security -n 3"

say_comment "Why containment is gated: the OPA matrix. Isolate is allowed at critical + active,"
say_comment "DENIED in kube-system, DENIED in observe-only."
play_cmd "keosctl policy test isolate_pod critical workloads active"
play_cmd "keosctl policy test isolate_pod critical kube-system active"

outro
