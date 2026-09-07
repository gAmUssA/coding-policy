#!/usr/bin/env bash
# Tests for mask-secrets.sh — emits ::add-mask:: for each >=16-char secret
# string in an auth.json, skipping short field values. Pure, deterministic.
#
# Run: bash .github/codex-review/tests/test_mask_secrets.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/../templates" && pwd)/mask-secrets.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: mask-secrets.sh not readable at $SCRIPT" >&2; exit 2; }

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }

AUTH_JSON='{"tokens":{"access_token":"sk-tokenABCDEF0123456789verylong","refresh_token":"rt-9876543210FEDCBAlongtoken","auth_mode":"chatgpt"},"last_refresh":"2026-07-19T00:00:00Z"}'

t_masks_long_tokens() {
  local dir; dir=$(mktemp -d); printf '%s' "$AUTH_JSON" > "$dir/auth.json"
  local out; out=$(bash "$SCRIPT" "$dir/auth.json")
  grep -qF "::add-mask::sk-tokenABCDEF0123456789verylong" <<<"$out" || { bad "masks access_token"; rm -rf "$dir"; return; }
  grep -qF "::add-mask::rt-9876543210FEDCBAlongtoken" <<<"$out"     || { bad "masks refresh_token"; rm -rf "$dir"; return; }
  ok "masks both long tokens"
  rm -rf "$dir"
}

t_skips_short_fields() {
  local dir; dir=$(mktemp -d); printf '%s' "$AUTH_JSON" > "$dir/auth.json"
  local out; out=$(bash "$SCRIPT" "$dir/auth.json")
  # "chatgpt" and the timestamp are short/borderline — must NOT be masked as words.
  grep -qF "::add-mask::chatgpt" <<<"$out" && { bad "must not mask short 'chatgpt'"; rm -rf "$dir"; return; }
  ok "skips short field values"
  rm -rf "$dir"
}

t_missing_file_ok() {
  local rc=0 out; out=$(bash "$SCRIPT" /nope/auth.json) || rc=$?
  if [[ $rc -eq 0 && -z "$out" ]]; then ok "missing file -> exit 0, no output"; else bad "missing file -> exit 0, no output (rc=$rc out='$out')"; fi
}

t_bad_args() {
  local rc=0; bash "$SCRIPT" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 2 ]]; then ok "no args -> exit 2"; else bad "no args -> exit 2 (rc=$rc)"; fi
}

echo "== mask-secrets.sh tests =="
t_masks_long_tokens
t_skips_short_fields
t_missing_file_ok
t_bad_args
echo "== summary: ${pass} passed, ${fail} failed =="
[[ "$fail" -eq 0 ]]
