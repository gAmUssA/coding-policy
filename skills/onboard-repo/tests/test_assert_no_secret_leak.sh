#!/usr/bin/env bash
# Tests for assert-no-secret-leak.sh — the guard that refuses to post a Codex
# review whose output contains the subscription credential. Pure file
# processing, no network. Deterministic (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_assert_no_secret_leak.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/../templates" && pwd)/assert-no-secret-leak.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: assert-no-secret-leak.sh not readable at $SCRIPT" >&2; exit 2; }

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

# A realistic auth.json shape: a long access/refresh token plus short fields.
AUTH_JSON='{"tokens":{"access_token":"sk-tokenABCDEF0123456789verylong","refresh_token":"rt-9876543210FEDCBAlongtoken","auth_mode":"chatgpt"},"last_refresh":"2026-07-19T00:00:00Z"}'

with_files() {
  # $1 = output-json contents; echoes "<auth> <out>" temp paths, caller cleans dir
  local dir; dir=$(mktemp -d)
  printf '%s' "$AUTH_JSON" > "$dir/auth.json"
  printf '%s' "$1" > "$dir/out.json"
  echo "$dir"
}

t_clean_passes() {
  local dir; dir=$(with_files '{"summary":"Policy loaded: 21 rule files from rules/. All good.","findings":[]}')
  local rc=0; bash "$SCRIPT" "$dir/auth.json" "$dir/out.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then ok "clean output passes"; else bad "clean output passes (rc=$rc)"; fi
  rm -rf "$dir"
}

t_leaked_access_token_fails() {
  local dir; dir=$(with_files '{"summary":"here is the token sk-tokenABCDEF0123456789verylong oops","findings":[]}')
  local rc=0; bash "$SCRIPT" "$dir/auth.json" "$dir/out.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "leaked access token -> exit 1"; else bad "leaked access token -> exit 1 (rc=$rc)"; fi
  rm -rf "$dir"
}

t_leaked_refresh_token_fails() {
  local dir; dir=$(with_files '{"summary":"rt-9876543210FEDCBAlongtoken","findings":[]}')
  local rc=0; bash "$SCRIPT" "$dir/auth.json" "$dir/out.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "leaked refresh token -> exit 1"; else bad "leaked refresh token -> exit 1 (rc=$rc)"; fi
  rm -rf "$dir"
}

# Short auth fields (e.g. "chatgpt") appearing in ordinary prose must NOT trip
# the guard — only >= 16-char secret material counts.
t_short_field_in_prose_passes() {
  local dir; dir=$(with_files '{"summary":"Reviewed the chatgpt workflow auth_mode handling.","findings":[]}')
  local rc=0; bash "$SCRIPT" "$dir/auth.json" "$dir/out.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then ok "short auth field in prose passes"; else bad "short auth field in prose passes (rc=$rc)"; fi
  rm -rf "$dir"
}

t_missing_files_pass() {
  local rc=0; bash "$SCRIPT" "/nope/auth.json" "/nope/out.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 0 ]]; then ok "missing files treated as clean"; else bad "missing files treated as clean (rc=$rc)"; fi
}

t_bad_args() {
  local rc=0; bash "$SCRIPT" only-one-arg >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 ]]; then ok "wrong arg count -> exit 2"; else bad "wrong arg count -> exit 2 (rc=$rc)"; fi
}

echo "== assert-no-secret-leak.sh tests =="
t_clean_passes
t_leaked_access_token_fails
t_leaked_refresh_token_fails
t_short_field_in_prose_passes
t_missing_files_pass
t_bad_args
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
