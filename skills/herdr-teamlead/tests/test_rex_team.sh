#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/rex-team.sh.
#
# Each case runs the real launcher with `env` as the command, so the
# assertions read the environment the lead would start with. No Rex server is
# contacted; the launcher only maps variables.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Inside Rex      -> the Herdr identity, both binaries and the backend marker.
#   2. Exit passthrough-> the started command's own status reaches the caller.
#   3. Outside Rex     -> exit 1, nothing started, either variable missing.
#   4. No command      -> exit 1 naming the usage.
#   5. Missing adapter -> exit 1 naming the reinstall command.
#
# Run: bash skills/herdr-teamlead/tests/test_rex_team.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

has_line() { printf '%s\n' "$1" | grep -qxF -- "$2"; }

main() {
  local skill script
  skill="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  script="${skill}/rex-team.sh"
  [[ -f "$script" && -r "$script" ]] || die "rex-team.sh not found/readable at $script"

  TMP="$(mktemp -d -t rex-team-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  FAIL=0; PASS=0
  local out rc
  local rex_env=(REX_SESSION=session:abc REX_BLOCK=block:lead)

  # 1. The lead starts with the Herdr identity mapped from Rex.
  out="$(env -u HERDR_ENV -u HERDR_PANE_ID "${rex_env[@]}" bash "$script" env 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && has_line "$out" "HERDR_ENV=1" && has_line "$out" "HERDR_PANE_ID=block:lead" \
     && has_line "$out" "HERDR_WORKSPACE_ID=session:abc" && has_line "$out" "HERDR_BIN=${skill}/rex-herdr.sh" \
     && has_line "$out" "TEAMLEAD_HERDR_BIN=${skill}/rex-herdr.sh" && has_line "$out" "TEAMLEAD_BACKEND=rex"; then
    pass; else fail "inside Rex: expected the mapped identity, got rc=$rc out=$out"; fi

  # 2. The command's own exit status passes through exec.
  out="$(env "${rex_env[@]}" bash "$script" sh -c 'exit 7' 2>&1)"; rc=$?
  if [[ $rc -eq 7 ]]; then pass; else fail "passthrough: expected exit 7, got rc=$rc out=$out"; fi

  # 3. Outside Rex nothing starts, whichever variable is missing.
  local marker="$TMP/started" kept
  for kept in REX_SESSION=session:abc REX_BLOCK=block:lead; do
    out="$(env -u REX_SESSION -u REX_BLOCK "$kept" bash "$script" touch "$marker" 2>&1)"; rc=$?
    if [[ $rc -eq 1 && ! -e "$marker" ]] && printf '%s' "$out" | grep -q "run this from a Rex terminal"; then
      pass; else fail "outside Rex (only $kept set): expected exit 1 and no start, got rc=$rc out=$out"; fi
  done

  # 4. No command is a usage error.
  out="$(env "${rex_env[@]}" bash "$script" 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "name the command to start"; then
    pass; else fail "no command: expected exit 1 with usage, got rc=$rc out=$out"; fi

  # 5. A launcher copied without its adapter refuses.
  mkdir -p "$TMP/bare" || die "could not create $TMP/bare"
  cp "$script" "$TMP/bare/rex-team.sh" || die "could not copy the launcher"
  out="$(env "${rex_env[@]}" bash "$TMP/bare/rex-team.sh" touch "$marker" 2>&1)"; rc=$?
  if [[ $rc -eq 1 && ! -e "$marker" ]] && printf '%s' "$out" | grep -q "tessl install gamussa/coding-policy"; then
    pass; else fail "missing adapter: expected exit 1 with the reinstall hint, got rc=$rc out=$out"; fi

  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
