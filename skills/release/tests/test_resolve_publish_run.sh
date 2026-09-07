#!/usr/bin/env bash
# Outcome-based tests for resolve-publish-run.sh.
#
# Covers behaviors the script promises:
#   1. Immediate hit — `gh run list` returns the run on the first call,
#      script emits {"database_id": N} and exits 0 without sleeping.
#   2. Deferred hit — first N calls return empty, then a later call
#      returns the run; script polls, eventually finds it, emits JSON,
#      exits 0.
#   3. Budget exhausted — every call returns empty; script exits non-zero
#      with a diagnostic on stderr that mentions the SHA and workflow.
#   4. Arg-count validation — missing args produce exit 2 with usage.
#   5. Env-var validation — non-positive-integer INTERVAL/BUDGET values
#      produce exit 2 with a clear diagnostic naming the bad var.
#   6. INTERVAL > BUDGET rejected.
#   7. Budget cap — total sleep never exceeds BUDGET_SEC even when
#      INTERVAL doesn't divide BUDGET evenly.
#   8. Numeric run-id validation — gh returning non-numeric output
#      produces exit 1 with an actionable diagnostic.
#
# Approach: source the script (the main() guard prevents auto-run when
# sourced) and override `gh` + `sleep` as shell functions. Because
# `main` runs the gh call inside a command substitution (`run_id=$(...)`),
# state like "which call is this" can't live in shell variables — the
# substitution spawns a subshell that gets its own copy and writes
# don't propagate back. State lives in tempfiles instead: a calls log
# the mock appends to, and a queue file the mock indexes into for each
# call's response.
#
# Run: bash skills/release/tests/test_resolve_publish_run.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/resolve-publish-run.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: resolve-publish-run.sh not executable at $SCRIPT" >&2; exit 2; }

# Override to 1s/3s so the budget-exhausted test stays fast. The script
# defaults (2s interval, 30s budget) are not directly observable in
# these tests — call counts and exit codes are what's asserted, not
# wall-clock timing. A separate test would be needed to cover defaults.
export RESOLVE_PUBLISH_RUN_INTERVAL_SEC=1
export RESOLVE_PUBLISH_RUN_BUDGET_SEC=3

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

# Tempfiles tracking mock state across subshell boundaries.
TMPDIR_TEST=$(mktemp -d -t resolve-pub-test.XXXXXX)
# Named handler ending `return 0`, not a bare `trap 'rm -rf ...'`: the
# EXIT trap's final command status becomes the process's exit status, so
# a failed cleanup would turn an all-green run non-zero and flake CI
# (rules/error-handling.md Shell Error Handling).
cleanup_tmp() {
  if [[ -n "${TMPDIR_TEST:-}" ]]; then
    if ! rm -rf "$TMPDIR_TEST"; then
      echo "warning: could not remove temp dir ${TMPDIR_TEST} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT
export MOCK_GH_CALLS_FILE="$TMPDIR_TEST/gh-calls"
export MOCK_SLEEP_CALLS_FILE="$TMPDIR_TEST/sleep-calls"
export MOCK_GH_QUEUE_FILE="$TMPDIR_TEST/gh-queue"

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Mock `gh` — stands in for `gh run list ... --jq '...'`. The real gh
# with --jq returns the filtered output as text, so the mock returns
# the next queued response verbatim (EMPTY → empty stdout, otherwise
# echoed). Records each invocation in MOCK_GH_CALLS_FILE so the test
# can count calls after main() returns.
gh() {
  [[ "$1" == "run" && "$2" == "list" ]] || { echo "mock gh: unexpected invocation: $*" >&2; return 99; }
  echo "call" >> "$MOCK_GH_CALLS_FILE"
  local call_count
  call_count=$(wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' ')
  local response
  response=$(sed -n "${call_count}p" "$MOCK_GH_QUEUE_FILE")
  [[ -z "$response" || "$response" == "EMPTY" ]] && return 0
  echo "$response"
}

# Mock `sleep` — record the requested duration (one per line) without
# actually waiting. Tests sum these to verify the loop respects the
# wall-clock budget.
sleep() {
  echo "$1" >> "$MOCK_SLEEP_CALLS_FILE"
}

reset_mocks() {
  : > "$MOCK_GH_CALLS_FILE"
  : > "$MOCK_SLEEP_CALLS_FILE"
  : > "$MOCK_GH_QUEUE_FILE"
  # Reset env-driven knobs to known-good values. Tests that exercise
  # invalid values override these immediately before invoking main()
  # below. Without this, an INTERVAL_SEC=0 override from one test
  # leaks into the next test's main() call (env-prefix on a `var=$(...)`
  # assignment is a plain variable assignment in bash, not a command-
  # scoped env override).
  INTERVAL_SEC=1
  BUDGET_SEC=3
  # shellcheck disable=SC2034  # read by the sourced resolve-publish-run.sh (--limit); shellcheck can't trace the source boundary
  RUN_LIST_LIMIT=100
}

queue_responses() {
  for r in "$@"; do
    echo "$r" >> "$MOCK_GH_QUEUE_FILE"
  done
}

gh_calls() { wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' '; }
sleep_calls() { wc -l < "$MOCK_SLEEP_CALLS_FILE" | tr -d ' '; }
total_sleep_seconds() { awk '{ sum += $1 } END { print sum + 0 }' "$MOCK_SLEEP_CALLS_FILE"; }

# Extract .database_id from a JSON envelope; prints empty if absent.
database_id_of() { echo "$1" | jq -r '.database_id // empty'; }

# --- Test 1: immediate hit ----------------------------------------------------
test_immediate_hit() {
  reset_mocks
  queue_responses "123456"
  local output rc=0
  output=$(main gAmUssA coding-policy abc123 publish.yml 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "123456" "$(database_id_of "$output")" || return 1
  assert_eq "gh call count" "1" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "0" "$(sleep_calls)" || return 1
}
run "immediate hit emits {\"database_id\": N} without sleeping" test_immediate_hit

# --- Test 2: deferred hit (poll succeeds on third try) ------------------------
test_deferred_hit() {
  reset_mocks
  queue_responses "EMPTY" "EMPTY" "789012"
  local output rc=0
  output=$(main gAmUssA coding-policy def456 publish.yml 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "789012" "$(database_id_of "$output")" || return 1
  assert_eq "gh call count" "3" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "2" "$(sleep_calls)" || return 1
}
run "deferred hit polls until run appears" test_deferred_hit

# --- Test 3: budget exhausted -------------------------------------------------
test_budget_exhausted() {
  reset_mocks
  queue_responses "EMPTY" "EMPTY" "EMPTY" "EMPTY" "EMPTY"
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy zzz999 publish.yml 2>&1 >/dev/null) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected non-zero exit, got 0" >&2; return 1; }
  echo "$stderr" | grep -q "zzz999" || { echo "    FAIL: stderr missing SHA, got: ${stderr}" >&2; return 1; }
  echo "$stderr" | grep -q "publish.yml" || { echo "    FAIL: stderr missing workflow name, got: ${stderr}" >&2; return 1; }
}
run "budget exhausted exits non-zero with diagnostic" test_budget_exhausted

# --- Test 4: arg count validation ---------------------------------------------
test_arg_validation() {
  reset_mocks
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy abc123 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "usage:" || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "missing arg exits 2 with usage" test_arg_validation

# --- Test 5: env-var validation (positive integer requirement) ----------------
test_interval_zero_rejected() {
  reset_mocks
  INTERVAL_SEC=0
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy abc publish.yml 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "INTERVAL_SEC" || { echo "    FAIL: stderr should name INTERVAL_SEC var, got: ${stderr}" >&2; return 1; }
}
run "INTERVAL_SEC=0 rejected with named diagnostic" test_interval_zero_rejected

test_budget_negative_rejected() {
  reset_mocks
  BUDGET_SEC=-5
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy abc publish.yml 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "BUDGET_SEC" || { echo "    FAIL: stderr should name BUDGET_SEC var, got: ${stderr}" >&2; return 1; }
}
run "BUDGET_SEC=-5 rejected with named diagnostic" test_budget_negative_rejected

# --- Test 6: INTERVAL > BUDGET rejected --------------------------------------
test_interval_gt_budget_rejected() {
  reset_mocks
  INTERVAL_SEC=10
  BUDGET_SEC=5
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy abc publish.yml 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "cannot exceed" || { echo "    FAIL: stderr should explain interval-vs-budget, got: ${stderr}" >&2; return 1; }
}
run "INTERVAL_SEC > BUDGET_SEC rejected" test_interval_gt_budget_rejected

# --- Test 7: budget cap — total sleep cannot exceed BUDGET_SEC ---------------
# With INTERVAL=2 and BUDGET=3, a naive `sleep $INTERVAL` after each
# poll would sleep twice (4s total). The script caps the final sleep
# at remaining-budget so total sleep <= BUDGET_SEC.
test_budget_cap_on_non_divisible_interval() {
  reset_mocks
  # shellcheck disable=SC2034  # read by the sourced resolve-publish-run.sh; shellcheck can't trace the source boundary
  INTERVAL_SEC=2
  BUDGET_SEC=3
  queue_responses "EMPTY" "EMPTY" "EMPTY" "EMPTY"
  local rc=0
  # Wrap main in a subshell so its `exit 1` on budget exhaustion
  # doesn't kill the test runner.
  ( main gAmUssA coding-policy abc publish.yml >/dev/null 2>&1 ) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected budget-exhausted non-zero exit" >&2; return 1; }
  local total
  total=$(total_sleep_seconds)
  [[ "$total" -le "$BUDGET_SEC" ]] || { echo "    FAIL: total sleep ${total}s exceeds budget ${BUDGET_SEC}s" >&2; return 1; }
}
run "budget cap: total sleep never exceeds BUDGET_SEC (non-divisible interval)" test_budget_cap_on_non_divisible_interval

# --- Test 8: numeric run-id validation ---------------------------------------
test_non_numeric_run_id_rejected() {
  reset_mocks
  queue_responses "not-a-number"
  local stderr rc=0
  stderr=$(main gAmUssA coding-policy abc publish.yml 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  echo "$stderr" | grep -q "expected numeric run id" || { echo "    FAIL: stderr should explain numeric validation, got: ${stderr}" >&2; return 1; }
}
run "non-numeric run id rejected with diagnostic" test_non_numeric_run_id_rejected

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
