#!/usr/bin/env bash
# Outcome-based tests for tessl-hygiene.sh: pins gAmUssA/* deps to latest and
# ensures the .gitignore block, without touching third-party pins or the
# committed AGENTS.md/CLAUDE.md/GEMINI.md entrypoints.
#
# Each case builds a throwaway git repo (the script requires a worktree) — no
# shared mutable state, order-independent.
#
# Run: bash skills/onboard-repo/tests/test_tessl_hygiene.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/tessl-hygiene.sh"

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
mkrepo() { git init -q "$1"; ( cd "$1" && git config user.email t@t && git config user.name t ); }

main() {
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not found at $SCRIPT" >&2; exit 2; }
  command -v jq  >/dev/null 2>&1 || { echo "fatal: jq required"  >&2; exit 2; }
  command -v git >/dev/null 2>&1 || { echo "fatal: git required" >&2; exit 2; }

  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  FAIL=0; PASS=0

  local d out cp_ver react_ver

  # 1. pins gAmUssA/* to latest, leaves third-party pins untouched.
  d="$TMP/1"; mkrepo "$d"
  cat > "$d/tessl.json" <<'JSON'
{
  "name": "gAmUssA/x",
  "mode": "vendored",
  "dependencies": {
    "gAmUssA/coding-policy": { "version": "0.3.99" },
    "tessl/npm-react": { "version": "19.2.0" }
  }
}
JSON
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"
  cp_ver=$(jq -r '.dependencies["gAmUssA/coding-policy"].version' "$d/tessl.json")
  react_ver=$(jq -r '.dependencies["tessl/npm-react"].version' "$d/tessl.json")
  if [[ "$cp_ver" == "latest" && "$react_ver" == "19.2.0" ]] \
     && printf '%s' "$out" | jq -e '.tessl_json=="pinned-latest"' >/dev/null; then pass
  else fail "pin: cp=$cp_ver react=$react_ver out=$out"; fi

  # 2. idempotent: already latest -> unchanged.
  d="$TMP/2"; mkrepo "$d"
  printf '{"dependencies":{"gAmUssA/coding-policy":{"version":"latest"}}}\n' > "$d/tessl.json"
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"
  if printf '%s' "$out" | jq -e '.tessl_json=="unchanged"' >/dev/null; then pass; else fail "idempotent pin: $out"; fi

  # 3. .gitignore created with the block when absent; AGENTS/CLAUDE/GEMINI NOT in it.
  d="$TMP/3"; mkrepo "$d"
  printf '{"dependencies":{}}\n' > "$d/tessl.json"
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"
  if printf '%s' "$out" | jq -e '.gitignore=="created"' >/dev/null \
     && grep -qF '.tessl/' "$d/.gitignore" \
     && grep -qF '.gemini/settings.json' "$d/.gitignore" \
     && ! grep -qxF 'AGENTS.md' "$d/.gitignore" \
     && ! grep -qxF 'CLAUDE.md' "$d/.gitignore" \
     && ! grep -qxF 'GEMINI.md' "$d/.gitignore"; then pass
  else fail "gitignore created/content: $out"; fi

  # 4. appends to an existing .gitignore lacking the marker, preserving prior lines.
  d="$TMP/4"; mkrepo "$d"
  printf '{"dependencies":{}}\n' > "$d/tessl.json"
  printf 'node_modules/\n' > "$d/.gitignore"
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"
  if printf '%s' "$out" | jq -e '.gitignore=="appended"' >/dev/null \
     && grep -qxF 'node_modules/' "$d/.gitignore" \
     && grep -qF '.tessl/' "$d/.gitignore"; then pass
  else fail "gitignore append: $out"; fi

  # 5. unchanged when the marker is already present.
  d="$TMP/5"; mkrepo "$d"
  printf '{"dependencies":{}}\n' > "$d/tessl.json"
  (cd "$d" && bash "$SCRIPT" >/dev/null 2>&1)   # first run adds the block
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"  # second run
  if printf '%s' "$out" | jq -e '.gitignore=="unchanged"' >/dev/null; then pass; else fail "gitignore idempotent: $out"; fi

  # 6. missing tessl.json -> tessl_json:absent, gitignore still written.
  d="$TMP/6"; mkrepo "$d"
  out="$(cd "$d" && bash "$SCRIPT" 2>/dev/null)"; local rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | jq -e '.tessl_json=="absent" and .gitignore=="created"' >/dev/null; then pass
  else fail "absent tessl.json: rc=$rc out=$out"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
