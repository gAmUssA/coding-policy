#!/usr/bin/env bash
# Outcome-based tests for hooks/bounded-run.sh.
#
# Covers:
#   1. Fast command      -> its stdout and exit code pass through unchanged.
#   2. Deadline passes   -> exit 124 within the deadline plus grace, a stderr
#                           diagnostic, and a grandchild in the group is dead.
#   3. TERM ignored      -> SIGKILL follows the grace period; exit 124.
#   4. Usage errors      -> a non-positive or missing deadline exits 125.
#
# Run: bash hooks/tests/test_bounded_run.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/bounded-run.sh"
  [[ -r "$SCRIPT" ]] || die "runner not found at $SCRIPT"
  TMP="$(mktemp -d -t bounded-run-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  local out rc start elapsed gc

  # 1. Pass-through.
  rc=0; out="$(bash "$SCRIPT" 5 bash -c 'echo hello; exit 7' </dev/null 2>/dev/null)" || rc=$?
  if [[ $rc -eq 7 && "$out" == "hello" ]]; then pass; else fail "pass-through: expected 'hello' and exit 7, got rc=$rc out=$out"; fi

  # 2. Deadline: the command spawns a grandchild sleeper and waits on it.
  start=$SECONDS; rc=0
  bash "$SCRIPT" 1 bash -c 'sleep 30 & echo $! > "$1"; wait' _ "$TMP/gc.pid" </dev/null 2>"$TMP/err2" || rc=$?
  elapsed=$(( SECONDS - start ))
  gc="$(cat "$TMP/gc.pid")" || die "grandchild pid not recorded"
  sleep 0.3
  if [[ $rc -eq 124 ]] && (( elapsed <= 5 )) && grep -q 'deadline' "$TMP/err2" && ! kill -0 "$gc" 2>/dev/null; then
    pass; else fail "deadline: expected 124 within 5s, a diagnostic, and a dead grandchild; got rc=$rc elapsed=$elapsed"; fi

  # 3. A command that ignores SIGTERM is killed after the grace period.
  start=$SECONDS; rc=0
  bash "$SCRIPT" 1 bash -c 'trap "" TERM; while :; do sleep 0.1; done' </dev/null 2>/dev/null || rc=$?
  elapsed=$(( SECONDS - start ))
  if [[ $rc -eq 124 ]] && (( elapsed <= 6 )); then pass; else fail "TERM ignored: expected 124 within 6s, got rc=$rc elapsed=$elapsed"; fi

  # 4. Usage errors.
  rc=0; bash "$SCRIPT" 0 true 2>/dev/null || rc=$?
  if [[ $rc -eq 125 ]]; then pass; else fail "usage: deadline 0 should exit 125, got $rc"; fi
  rc=0; bash "$SCRIPT" 5 2>/dev/null || rc=$?
  if [[ $rc -eq 125 ]]; then pass; else fail "usage: missing command should exit 125, got $rc"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
