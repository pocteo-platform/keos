#!/usr/bin/env bash
# Security Episode 1 — Reverse shell: detect → contain → escalate.
source "$(dirname "$0")/cast-lib.sh"

intro "Security · Episode 1 · Reverse shell" "detect → isolate → escalate"

say_comment "Keos runs a second domain: runtime security. Set it active so it can contain."
play_cmd "keosctl mode active"

say_comment "The security scenario library — runtime threats, not reliability incidents"
play_cmd "keosctl usecases list --domain security"

say_comment "Fire the reverse-shell scenario. On eBPF hardware this runs /bin/sh -i"
say_comment "inside the live pod and Tetragon/Falco detect it for real."
play_cmd "keosctl run lib-reverse-shell"

pause 3
say_comment "The agent classified it security, diagnosed it, and gated containment via OPA"
play_cmd "keosctl events --domain security -n 5"

say_comment "Containment is reversible + auditable: a deny-all NetworkPolicy + quarantine label,"
say_comment "then page_security. The whole chain is HMAC-signed."
play_cmd "keosctl audit verify"

outro
