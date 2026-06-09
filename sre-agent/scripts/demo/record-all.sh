#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  record-all.sh — record every episode to .cast + convert to .gif
# ─────────────────────────────────────────────────────────────────────────────
#  Usage:  bash record-all.sh [out-dir]      (default: ./out)
#  Requires: asciinema, agg, a live keosctl install in the current context.
# ─────────────────────────────────────────────────────────────────────────────
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/out}"
mkdir -p "$OUT"

# Normalise line endings (repo may be checked out with CRLF on Windows).
sed -i 's/\r$//' "$HERE"/*.sh

for ep in ep01-status ep02-usecase ep03-introspection ep04-day2 ep05-help; do
  echo "=== Recording $ep ==="
  rm -f "$OUT/$ep.cast"
  asciinema rec --cols 100 --rows 32 --overwrite \
    -c "bash $HERE/$ep.sh" "$OUT/$ep.cast"
  echo "=== Converting $ep.gif ==="
  agg --theme monokai --font-size 16 --speed 1.0 \
    "$OUT/$ep.cast" "$OUT/$ep.gif"
done

echo
echo "Done. Artifacts:"
ls -lh "$OUT"
