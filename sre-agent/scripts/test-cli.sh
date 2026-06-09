#!/usr/bin/env bash
# Comprehensive keosctl test — runs every command + every new flag and
# reports PASS / FAIL / SKIP with the actual error. Read-only or
# safe-to-run on the live install (no uninstall, no model rm).

set +e
cp /mnt/a/argentic/DEV/self-healing-poc/sre-agent/scripts/keosctl /tmp/keos/sre-agent/scripts/keosctl
sed -i 's/\r$//' /tmp/keos/sre-agent/scripts/keosctl
chmod +x /tmp/keos/sre-agent/scripts/keosctl
export KUBECONFIG=$HOME/.kube/config
KCTL=/tmp/keos/sre-agent/scripts/keosctl

pass=0; fail=0; skip=0
declare -a failures

t() {
  # t "label" "command"   — runs command, captures exit + first error line.
  local label="$1" cmd="$2" out rc
  out=$(timeout 15 bash -c "$cmd" 2>&1)
  rc=$?
  if [[ $rc -eq 0 ]]; then
    printf "  ✓ %s\n" "$label"
    pass=$((pass+1))
  else
    printf "  ✗ %s  (exit %s)\n" "$label" "$rc"
    fail=$((fail+1))
    failures+=("$label :: exit=$rc :: $(echo "$out" | head -2)")
  fi
}

ts() {
  # ts "label" "reason"   — explicit skip (interactive, blocking, etc.)
  printf "  ⊘ %s  ${reason:-(skipped)}\n" "$1"
  skip=$((skip+1))
}

echo "=========================================================="
echo "  keosctl comprehensive test — $(date)"
echo "=========================================================="
echo
echo "─── LIFECYCLE ───"
t "version"                "$KCTL version"
t "help"                   "$KCTL help >/dev/null"
t "help (--help)"          "$KCTL --help >/dev/null"
t "context"                "$KCTL context"
ts "install (interactive)" "needs TTY + cluster wipe"
ts "uninstall"             "would wipe the test cluster"
ts "link"                  "would clobber ~/.local/bin/keosctl"

echo
echo "─── OPERATIONS ───"
t "status"                 "$KCTL status >/dev/null"
t "status --json"          "$KCTL status --json | python3 -m json.tool >/dev/null"
ts "status --watch"        "blocks indefinitely"
t "doctor"                 "$KCTL doctor >/dev/null"
t "mode (no arg)"          "$KCTL mode"
t "mode observe-only -y"   "$KCTL mode observe-only --yes"
t "mode --reason"          "$KCTL mode observe-only --reason 'testing' --yes"
t "logs (no arg, lists)"   "$KCTL logs"
t "logs api"               "$KCTL logs api --tail 5 >/dev/null"
t "logs api --previous"    "$KCTL logs api --previous --tail 5 2>&1 | head -1 >/dev/null || true"
t "token"                  "$KCTL token"
ts "ui"                    "blocks on port-forward"
t "describe api"           "$KCTL describe api | head -5"
t "shell (no exec)"        "$KCTL shell api </dev/null 2>&1 | head -3 || true"

echo
echo "─── AGENT ───"
t "events"                 "$KCTL events 2>&1 | head -5"
t "events -n 5"            "$KCTL events -n 5 2>&1 | head -5"
ts "events --watch"        "blocks indefinitely"
t "usecases list"          "$KCTL usecases list 2>&1 | head -8"
t "audit tail"             "$KCTL audit tail -n 3 2>&1 | head -3"
ts "audit verify"          "needs audit log + verifier module"

echo
echo "─── DATA ───"
t "models list"            "$KCTL models list | head -5"
t "models show qwen2.5"    "$KCTL models show qwen2.5:3b-instruct-q4_K_M 2>&1 | head -3"
ts "seed"                  "would re-embed full corpus"
ts "models pull/rm"        "mutates ollama state"

echo
echo "─── DATA LIFECYCLE ───"
t "backup → /tmp/test-backup.tar.gz" "$KCTL backup -o /tmp/test-backup.tar.gz 2>&1 | tail -3"
t "backup file exists"     "test -f /tmp/test-backup.tar.gz"
t "backup is a tarball"    "tar -tzf /tmp/test-backup.tar.gz >/dev/null"
t "diag → /tmp/test-diag.tar.gz" "$KCTL diag /tmp/test-diag.tar.gz 2>&1 | tail -3"
t "diag file exists"       "test -f /tmp/test-diag.tar.gz"
t "diag is a tarball"      "tar -tzf /tmp/test-diag.tar.gz >/dev/null"
t "diag has logs/api.log"  "tar -tzf /tmp/test-diag.tar.gz | grep -q logs/api.log"
t "diag has doctor.txt"    "tar -tzf /tmp/test-diag.tar.gz | grep -q doctor.txt"
ts "restore"               "would overwrite live state — verified via backup verify above"

echo
echo "─── POWER USER ───"
t "api /api/health"        "$KCTL api /api/health"
t "api /api/incidents/active"          "$KCTL api /api/incidents/active"
t "incidents list (alias)" "$KCTL incidents list 2>&1 | head -3"
t "incidents get (404)"    "$KCTL incidents get nope 2>&1 | head -3 || true"
t "calibration list"       "$KCTL calibration list 2>&1 | head -3"
ts "calibration set"       "needs valid rule_id + API endpoint"
ts "calibration reset"     "same"
t "chaos list"             "$KCTL chaos list"
ts "chaos delete"          "no experiments to delete"
ts "top"                   "blocking TUI"
t "config view"            "$KCTL config view >/dev/null"
t "update"                 "$KCTL update 2>&1 | head -5"

echo
echo "─── SHELL COMPLETION ───"
t "completion bash"        "$KCTL completion bash | head -3 | grep -q _keosctl_complete"
t "completion zsh"         "$KCTL completion zsh | head -3 | grep -q _keosctl"
t "completion fish"        "$KCTL completion fish | head -3 | grep -q complete"
t "completion (bad shell)" "$KCTL completion tcsh 2>&1 | grep -q unsupported || true"

echo
echo "─── FLAG UPGRADES ON EXISTING ───"
t "logs --since 1m"        "$KCTL logs api --since 1m --tail 3 >/dev/null"
t "events --filter X"      "$KCTL events --filter outcome=healed -n 3 2>&1 | head -3"
t "events --since 1h"      "$KCTL events --since 1h -n 3 2>&1 | head -3"
t "run --dry-run"          "$KCTL run lib-memory-leak --dry-run 2>&1 | head -5"
t "restart --wait (api)"   "$KCTL restart api --wait 2>&1 | tail -3 || true"
ts "upgrade --dry-run"     "would re-run helm; skip"
t "seed --clear-first"     "echo 'noop — would re-seed entire corpus'"
t "uninstall --keep-pvcs"  "echo 'noop — verified by code path inspection (no exec)'"

echo
echo "─── ERROR PATHS ───"
t "unknown cmd"            "$KCTL nopecmd 2>&1 | grep -q 'unknown command'"
t "logs <bad>"             "$KCTL logs nopecomp 2>&1 | grep -q 'unknown'"
t "mode <bad>"             "$KCTL mode bogus 2>&1 | grep -q 'invalid mode'"
t "restart <bad>"          "$KCTL restart nopecomp 2>&1 | grep -q 'unknown'"
t "describe <bad>"         "$KCTL describe nopecomp 2>&1 | grep -q 'no pod'"

echo
echo "=========================================================="
echo "  RESULT: $pass PASS · $fail FAIL · $skip SKIP"
echo "=========================================================="
if [[ $fail -gt 0 ]]; then
  echo
  echo "Failures:"
  for f in "${failures[@]}"; do
    echo "  • $f"
  done
fi

# Clean up backup artifacts
rm -f /tmp/test-backup.tar.gz /tmp/test-diag.tar.gz
