#!/usr/bin/env bash
# Outcome-based tests for watch-pr-reviews.sh.
#
# Covers the behaviors the watcher promises:
#   1. Ready on first poll — mergeable, CI success, both bots posted, the policy
#      reviewer not CHANGES_REQUESTED (Copilot may be) → "ready", exit 0.
#   2. Deferred ready — first snapshot pending (one bot not yet posted), a
#      later snapshot ready → polls, reaches "ready", exit 0.
#   3. Both-bots-required — a lone codex verdict with copilot still "none"
#      is NOT ready (proves the watcher waits for the full merge-gate set,
#      not the first verdict to land).
#   4. Zero inline comments is still ready — a clean verdict with no inline
#      comments is complete; the watcher does not wait for comments.
#   5. ci "none" (no checks configured) is accepted as ready.
#   6. changes_requested — the policy reviewer's CHANGES_REQUESTED is terminal.
#  6b. Copilot advisory — a Copilot CHANGES_REQUESTED does NOT gate; with codex
#      approved + mergeable it is "ready" (rules/review-severity.md).
#   7. ci_failure — a failed check is terminal.
#   8. dirty — CONFLICTING mergeability is terminal.
#   9. pending_at_budget — every poll pending → exit 1, snapshot preserved.
#  10. Arg-count validation → exit 2 with usage.
#  11. Env-var validation — INTERVAL=0 and INTERVAL>BUDGET → exit 2.
#  12. Poll failure → exit 2.
#  13. Non-JSON snapshot → exit 2.
#
# Approach mirrors test_resolve_publish_run.sh: source the script (the main()
# guard prevents auto-run) and override `fetch_snapshot` + `sleep`. Because
# main() runs fetch_snapshot inside a command substitution (its own subshell),
# "which call is this" can't live in a shell variable — state lives in a
# tempfile the mock appends to and indexes into for each call's queued
# snapshot.
#
# Run: bash skills/release/tests/test_watch_pr_reviews.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/watch-pr-reviews.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: watch-pr-reviews.sh not found at $SCRIPT" >&2; exit 2; }

command -v jq >/dev/null 2>&1 || { echo "fatal: jq is required to run these tests" >&2; exit 2; }

# Fast, deterministic loop knobs. Call counts and exit codes are asserted,
# not wall-clock timing.
export WATCH_PR_REVIEWS_INTERVAL_SEC=1
export WATCH_PR_REVIEWS_BUDGET_SEC=3

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

TMPDIR_TEST=$(mktemp -d -t watch-pr-test.XXXXXX)
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
export MOCK_CALLS_FILE="$TMPDIR_TEST/calls"
export MOCK_QUEUE_FILE="$TMPDIR_TEST/queue"

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Override the snapshot source: return the next queued snapshot verbatim.
# A line "POLLFAIL" makes the mock exit non-zero (poll-pr-reviews.sh error);
# a line "GARBAGE" returns non-JSON. Records each call so tests can count polls.
fetch_snapshot() {
  echo "call" >> "$MOCK_CALLS_FILE"
  local n; n=$(wc -l < "$MOCK_CALLS_FILE" | tr -d ' ')
  local line; line=$(sed -n "${n}p" "$MOCK_QUEUE_FILE")
  # After the queue is exhausted, keep returning the last line so a
  # pending-forever queue drives the budget path deterministically.
  [[ -z "$line" ]] && line=$(tail -n 1 "$MOCK_QUEUE_FILE")
  case "$line" in
    POLLFAIL) return 1 ;;
    GARBAGE)  echo "not json"; return 0 ;;
    *)        echo "$line"; return 0 ;;
  esac
}

# Override sleep — record without waiting.
sleep() { echo "$1" >> "$TMPDIR_TEST/sleeps"; }

reset_mocks() {
  : > "$MOCK_CALLS_FILE"; : > "$MOCK_QUEUE_FILE"; : > "$TMPDIR_TEST/sleeps"
  INTERVAL_SEC=1
  BUDGET_SEC=3
}

queue() { for s in "$@"; do echo "$s" >> "$MOCK_QUEUE_FILE"; done; }
calls() { wc -l < "$MOCK_CALLS_FILE" | tr -d ' '; }
sleeps() { [[ -f "$TMPDIR_TEST/sleeps" ]] || { echo 0; return; }; wc -l < "$TMPDIR_TEST/sleeps" | tr -d ' '; }

# Build a compact snapshot JSON. Args: mergeable mstatus ci codex copilot [gh_comments copilot_comments]
snap() {
  local mergeable="$1" mstatus="$2" ci="$3" codex="$4" copilot="$5"
  local ghc="${6:-0}" cpc="${7:-0}"
  jq -cn \
    --arg mergeable "$mergeable" --arg mstatus "$mstatus" --arg ci "$ci" \
    --arg codex "$codex" --arg copilot "$copilot" \
    --argjson ghc "$ghc" --argjson cpc "$cpc" \
    '{
      pr_number: 42,
      ci: {status: $ci, checks: []},
      reviews: {
        codex:   {state: $codex,   submitted_at: null, body: null},
        copilot: {state: $copilot, submitted_at: null, body: null}
      },
      inline_comments: {codex: $ghc, copilot: $cpc},
      merge_state: {status: $mstatus, mergeable: $mergeable}
    }'
}

result_of() { echo "$1" | jq -r '.watch.result // empty'; }

# --- Test 1: ready on first poll ---------------------------------------------
test_ready_first_poll() {
  reset_mocks
  queue "$(snap MERGEABLE CLEAN success APPROVED APPROVED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)" || return 1
  assert_eq "sleep count" "0" "$(sleeps)" || return 1
}
run "ready on first poll — no sleep" test_ready_first_poll

# --- Test 2: deferred ready ---------------------------------------------------
test_deferred_ready() {
  reset_mocks
  queue \
    "$(snap MERGEABLE CLEAN success APPROVED none)" \
    "$(snap MERGEABLE CLEAN success APPROVED APPROVED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "2" "$(calls)" || return 1
  assert_eq "sleep count" "1" "$(sleeps)" || return 1
}
run "deferred ready polls until both bots post" test_deferred_ready

# --- Test 3: both bots required (a lone verdict is not ready) -----------------
test_both_bots_required() {
  reset_mocks
  # Copilot never posts — mergeable + codex approved is NOT enough.
  queue "$(snap MERGEABLE CLEAN success APPROVED none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code (budget)" "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")" || return 1
}
run "lone codex verdict is not ready — waits for both bots" test_both_bots_required

# --- Test 4: zero inline comments is still ready ------------------------------
test_zero_comments_ready() {
  reset_mocks
  queue "$(snap MERGEABLE CLEAN success COMMENTED APPROVED 0 0)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)" || return 1
}
run "zero inline comments is a complete review — ready" test_zero_comments_ready

# --- Test 5: ci none accepted as ready ---------------------------------------
test_ci_none_ready() {
  reset_mocks
  queue "$(snap MERGEABLE CLEAN none APPROVED APPROVED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
}
run "ci 'none' (no checks) counts as ready" test_ci_none_ready

# --- Test 6: changes_requested is terminal -----------------------------------
test_changes_requested() {
  reset_mocks
  queue "$(snap MERGEABLE BLOCKED success CHANGES_REQUESTED APPROVED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)" || return 1
}
run "changes_requested is a terminal verdict" test_changes_requested

# --- Test 6b: Copilot is always advisory — its CHANGES_REQUESTED does NOT gate -
test_copilot_advisory_not_terminal() {
  reset_mocks
  # Copilot CHANGES_REQUESTED with codex approved + mergeable is READY, not
  # changes_requested — only the policy reviewer gates (rules/review-severity.md).
  queue "$(snap MERGEABLE CLEAN success APPROVED CHANGES_REQUESTED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
}
run "Copilot CHANGES_REQUESTED does not gate — advisory only" test_copilot_advisory_not_terminal

# --- Test 7: ci_failure is terminal ------------------------------------------
test_ci_failure() {
  reset_mocks
  queue "$(snap MERGEABLE UNSTABLE failure none none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ci_failure" "$(result_of "$out")" || return 1
}
run "ci failure is terminal (before waiting on reviews)" test_ci_failure

# --- Test 8: dirty is terminal -----------------------------------------------
test_dirty() {
  reset_mocks
  queue "$(snap CONFLICTING DIRTY none none none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "dirty" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)" || return 1
}
run "conflicting branch surfaces as dirty" test_dirty

# --- Test 9: pending_at_budget preserves the snapshot ------------------------
test_pending_at_budget() {
  reset_mocks
  queue "$(snap UNKNOWN BLOCKED pending none none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")" || return 1
  # The final snapshot is preserved so the agent can see which field is stuck.
  assert_eq "snapshot preserved (codex)" "none" "$(echo "$out" | jq -r '.reviews.codex.state')" || return 1
  assert_eq "snapshot preserved (ci)" "pending" "$(echo "$out" | jq -r '.ci.status')" || return 1
}
run "pending forever exits 1 with the snapshot intact" test_pending_at_budget

# --- Test 10: arg-count validation -------------------------------------------
test_arg_validation() {
  reset_mocks
  local err rc=0
  err=$(main gamussa coding-policy 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$err" | grep -q "usage:" || { echo "    FAIL: missing usage line, got: ${err}" >&2; return 1; }
}
run "missing arg exits 2 with usage" test_arg_validation

# --- Test 11: env-var validation ---------------------------------------------
test_interval_zero_rejected() {
  reset_mocks
  INTERVAL_SEC=0
  local err rc=0
  err=$(main gamussa coding-policy 42 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$err" | grep -q "WATCH_PR_REVIEWS_INTERVAL_SEC" || { echo "    FAIL: should name INTERVAL var, got: ${err}" >&2; return 1; }
}
run "INTERVAL_SEC=0 rejected with named diagnostic" test_interval_zero_rejected

test_interval_gt_budget_rejected() {
  reset_mocks
  # shellcheck disable=SC2034  # read by the sourced watch-pr-reviews.sh; shellcheck can't trace the source boundary
  INTERVAL_SEC=10
  # shellcheck disable=SC2034  # read by the sourced watch-pr-reviews.sh; shellcheck can't trace the source boundary
  BUDGET_SEC=5
  local err rc=0
  err=$(main gamussa coding-policy 42 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$err" | grep -q "cannot exceed" || { echo "    FAIL: should explain interval-vs-budget, got: ${err}" >&2; return 1; }
}
run "INTERVAL_SEC > BUDGET_SEC rejected" test_interval_gt_budget_rejected

# --- Test 12: poll failure surfaces as rc 2 ----------------------------------
test_poll_failure() {
  reset_mocks
  queue "POLLFAIL"
  local err rc=0
  err=$(main gamussa coding-policy 42 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$err" | grep -q "poll-pr-reviews.sh failed" || { echo "    FAIL: should name poll failure, got: ${err}" >&2; return 1; }
}
run "poll-pr-reviews.sh failure exits 2" test_poll_failure

# --- Test 13: non-JSON snapshot surfaces as rc 2 -----------------------------
test_non_json() {
  reset_mocks
  queue "GARBAGE"
  local err rc=0
  err=$(main gamussa coding-policy 42 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$err" | grep -q "non-JSON" || { echo "    FAIL: should flag non-JSON, got: ${err}" >&2; return 1; }
}
run "non-JSON snapshot exits 2" test_non_json

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
