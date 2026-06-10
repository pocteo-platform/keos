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

# SRE-domain episodes + the Phase 21 security-domain series. The sec-*
# episodes drive the security domain (reverse shell, miner, priv-esc, exfil)
# — on the dev box they use synthetic events; on eBPF workshop hardware the
# same scenarios fire from live Falco/Tetragon.
for ep in ep01-status ep02-usecase ep03-introspection ep04-day2 ep05-help \
          sec-ep01-reverse-shell sec-ep02-crypto-miner sec-ep03-priv-esc sec-ep04-data-exfil; do
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
