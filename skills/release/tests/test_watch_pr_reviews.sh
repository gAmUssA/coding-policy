#!/usr/bin/env bash
# Outcome-based tests for watch-pr-reviews.sh.
#
# Covers the behaviors the watcher promises:
#   1. Ready on first poll — mergeable, CI success, the policy reviewer posted
#      and not CHANGES_REQUESTED → "ready", exit 0.
#   2. Deferred ready — first snapshot pending (policy reviewer not yet
#      posted), a later snapshot ready → polls, reaches "ready", exit 0.
#   3. Policy-verdict-required — mergeable and green with the policy reviewer
#      still "none" is NOT ready.
#   4. Zero inline comments is still ready — a clean verdict with no inline
#      comments is complete; the watcher does not wait for comments.
#   5. ci "none" (no checks configured) is accepted as ready.
#   6. changes_requested — the policy reviewer's CHANGES_REQUESTED is terminal.
#   7. ci_failure — a failed check is terminal.
#   8. dirty — CONFLICTING mergeability is terminal.
#   9. pending_at_budget — every poll pending → exit 1, snapshot preserved.
#  10. Arg-count validation → exit 2 with usage.
#  11. Env-var validation — INTERVAL=0 and INTERVAL>BUDGET → exit 2.
#  12. Poll failure → exit 2.
#  13. Non-JSON snapshot → exit 2.
#  14. Cancelled checks, end to end — the real poll-pr-reviews.sh behind the
#      real entry point, with a fake `gh` on PATH: an unreplaced cancel is
#      never ready (pending_at_budget); a cancel replaced by a pass is ready.
#  15. Review lanes — per-lane gating; an advisory lane never gates or holds
#      ready; a Copilot review (no lane) is ignored end to end.
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

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

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

# Build a compact snapshot JSON. Args: mergeable mstatus ci codex [codex_comments]
snap() {
  local mergeable="$1" mstatus="$2" ci="$3" codex="$4"
  local ghc="${5:-0}"
  jq -cn \
    --arg mergeable "$mergeable" --arg mstatus "$mstatus" --arg ci "$ci" \
    --arg codex "$codex" --argjson ghc "$ghc" \
    '{
      pr_number: 42,
      ci: {status: $ci, checks: []},
      reviews: {
        codex: {state: $codex, submitted_at: null, body: null, requested: false}
      },
      inline_comments: {codex: $ghc},
      merge_state: {status: $mstatus, mergeable: $mergeable}
    }'
}

result_of() { echo "$1" | jq -r '.watch.result // empty'; }

# --- Test 1: ready on first poll ---------------------------------------------
test_ready_first_poll() {
  reset_mocks
  queue "$(snap MERGEABLE CLEAN success APPROVED)"
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
    "$(snap MERGEABLE CLEAN success none)" \
    "$(snap MERGEABLE CLEAN success COMMENTED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "2" "$(calls)" || return 1
  assert_eq "sleep count" "1" "$(sleeps)" || return 1
}
run "deferred ready polls until the policy reviewer posts" test_deferred_ready

# --- Test 3: the policy verdict is required -----------------------------------
test_policy_verdict_required() {
  reset_mocks
  # Mergeable and green, but the policy reviewer never posts — NOT ready.
  queue "$(snap MERGEABLE CLEAN success none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code (budget)" "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")" || return 1
}
run "no policy verdict is not ready — waits for it" test_policy_verdict_required

# --- Test 4: zero inline comments is still ready ------------------------------
test_zero_comments_ready() {
  reset_mocks
  queue "$(snap MERGEABLE CLEAN success COMMENTED 0)"
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
  queue "$(snap MERGEABLE CLEAN none APPROVED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
}
run "ci 'none' (no checks) counts as ready" test_ci_none_ready

# --- Test 6: changes_requested is terminal -----------------------------------
test_changes_requested() {
  reset_mocks
  queue "$(snap MERGEABLE BLOCKED success CHANGES_REQUESTED)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)" || return 1
}
run "changes_requested is a terminal verdict" test_changes_requested

# --- Test 7: ci_failure is terminal ------------------------------------------
test_ci_failure() {
  reset_mocks
  queue "$(snap MERGEABLE UNSTABLE failure none)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ci_failure" "$(result_of "$out")" || return 1
}
run "ci failure is terminal (before waiting on reviews)" test_ci_failure

# --- Test 8: dirty is terminal -----------------------------------------------
test_dirty() {
  reset_mocks
  queue "$(snap CONFLICTING DIRTY none none)"
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
  queue "$(snap UNKNOWN BLOCKED pending none)"
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

# --- Test 14: cancelled checks through the real entry point ------------------
# Runs `bash watch-pr-reviews.sh` as a subprocess, so the real fetch_snapshot
# calls the real poll-pr-reviews.sh. Only `gh` is faked, at the process
# boundary: it refuses any call not pinned to github.com and answers with the
# `gh pr checks --json name,bucket` shape GitHub returns. The policy reviewer
# posted at head (beside a Copilot review no lane claims) and the branch is
# mergeable, so ci.status alone decides ready.
# Every setup step is checked: a half-built fake would let PATH fall through
# to the real `gh` and run these cases against GitHub.
FAKE_BIN="$TMPDIR_TEST/bin"
mkdir -p "$FAKE_BIN" \
  || { echo "fatal: could not create the fake gh directory ${FAKE_BIN} — check that ${TMPDIR_TEST} is writable" >&2; exit 2; }
cat > "$FAKE_BIN/gh" <<'FAKEGH' \
  || { echo "fatal: could not write the fake gh at ${FAKE_BIN}/gh — check free space and permissions under ${TMPDIR_TEST}" >&2; exit 2; }
#!/usr/bin/env bash
set -euo pipefail
head="0123456789abcdef0123456789abcdef01234567"
args=" $* "
if [[ "$1" == api ]]; then
  [[ "$args" == *" --hostname github.com "* ]] || { echo "fake gh: unpinned api call: $*" >&2; exit 97; }
else
  [[ "$args" == *" --repo github.com/"* ]] || { echo "fake gh: unpinned call: $*" >&2; exit 97; }
fi
case "$1 ${2:-}" in
  "pr view")   echo '{"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE","headRefOid":"'"$head"'"}' ;;
  "pr checks") cat "$FAKE_GH_CHECKS_FILE" ;;
  "api --hostname")
    if [[ "$args" == *" graphql "* ]]; then
      filter=""
      while [[ $# -gt 0 ]]; do [[ "$1" == --jq ]] && filter="$2"; shift; done
      echo '{"data":{"repository":{"pullRequest":{"reviewRequests":{"nodes":[]}}}}}' | jq "$filter"
    elif [[ "$args" == *"/reviews"* && -n "${FAKE_GH_REVIEWS_FILE:-}" ]]; then
      sed "s/@HEAD@/${head}/g" "$FAKE_GH_REVIEWS_FILE"
    elif [[ "$args" == *"/reviews"* ]]; then
      echo '[{"id":1,"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-10-20T10:00:00Z","body":"ok","commit_id":"'"$head"'"},{"id":2,"user":{"login":"copilot-pull-request-reviewer[bot]"},"state":"COMMENTED","submitted_at":"2026-10-20T10:01:00Z","body":"ok","commit_id":"'"$head"'"}]'
    else
      echo '[]'
    fi ;;
  *) echo "fake gh: unsupported: $*" >&2; exit 2 ;;
esac
FAKEGH
chmod +x "$FAKE_BIN/gh" \
  || { echo "fatal: could not make ${FAKE_BIN}/gh executable — check permissions under ${TMPDIR_TEST}" >&2; exit 2; }
FAKE_GH_RESOLVED=$(PATH="$FAKE_BIN:$PATH" command -v gh) \
  || { echo "fatal: no gh resolves on PATH after installing the fake at ${FAKE_BIN}/gh — inspect that file" >&2; exit 2; }
[[ "$FAKE_GH_RESOLVED" == "$FAKE_BIN/gh" ]] \
  || { echo "fatal: PATH resolves gh to ${FAKE_GH_RESOLVED}, not the fake at ${FAKE_BIN}/gh — refusing to run against the real gh" >&2; exit 2; }
WATCH_ERR_FILE="$TMPDIR_TEST/watch.err"

watch_with_checks() {
  local checks="$1"
  printf '%s' "$checks" > "$TMPDIR_TEST/checks.json" \
    || { echo "    FAIL: could not write the checks fixture to ${TMPDIR_TEST}/checks.json — check that ${TMPDIR_TEST} is writable" >&2; return 2; }
  # stderr goes to a file, not /dev/null: assert_watch_rc prints it when the
  # exit is unexpected, so a failing case shows the watcher's own diagnostic.
  FAKE_GH_CHECKS_FILE="$TMPDIR_TEST/checks.json" PATH="$FAKE_BIN:$PATH" \
    bash "$SCRIPT" gamussa coding-policy 42 2>"$WATCH_ERR_FILE"
}

# assert_eq on the exit code, plus the watcher's captured stderr on mismatch.
assert_watch_rc() {
  local expected="$1" actual="$2"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: exit code: expected '${expected}', got '${actual}'; watcher stderr:" >&2
  if [[ -r "$WATCH_ERR_FILE" && ! -s "$WATCH_ERR_FILE" ]]; then
    echo "      (empty)" >&2
  elif [[ -r "$WATCH_ERR_FILE" ]]; then
    sed 's/^/      /' "$WATCH_ERR_FILE" >&2
  else
    echo "      (no stderr captured at ${WATCH_ERR_FILE})" >&2
  fi
  return 1
}

test_e2e_unreplaced_cancel_never_ready() {
  local out rc=0
  out=$(watch_with_checks '[{"bucket":"cancel","name":"unit-tests"},{"bucket":"pass","name":"review"},{"bucket":"pass","name":"lint"},{"bucket":"pass","name":"diagnostics"}]') || rc=$?
  assert_watch_rc "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")" || return 1
  assert_eq "ci" "pending" "$(echo "$out" | jq -r '.ci.status')" || return 1
}
run "e2e: an unreplaced cancelled check is never ready" test_e2e_unreplaced_cancel_never_ready

test_e2e_replaced_cancel_is_ready() {
  local out rc=0
  out=$(watch_with_checks '[{"bucket":"cancel","name":"unit-tests"},{"bucket":"pass","name":"unit-tests"},{"bucket":"pass","name":"review"},{"bucket":"pass","name":"lint"}]') || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
}
run "e2e: a cancel replaced by a pass is ready" test_e2e_replaced_cancel_is_ready

test_e2e_replaced_by_fail_is_ci_failure() {
  local out rc=0
  out=$(watch_with_checks '[{"bucket":"cancel","name":"unit-tests"},{"bucket":"fail","name":"unit-tests"},{"bucket":"pass","name":"review"}]') || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "ci_failure" "$(result_of "$out")" || return 1
}
run "e2e: a cancel replaced by a fail is ci_failure" test_e2e_replaced_by_fail_is_ci_failure

# --- Test 15: review lanes through the real entry point (ADR 0010) ----------
# Real-shaped GitHub review objects, every one bound to head (@HEAD@ is
# substituted by the fake gh). The Codex reviewer and a Claude reviewer both
# post as github-actions[bot]; only the body marker tells them apart. The
# Copilot reviews in these fixtures belong to no lane and must change nothing
# (docs/adr/0014-retire-copilot-lane.md).
CODEX_MARK='<!-- coding-policy:codex-policy-review:v1 -->'
CLAUDE_MARK='<!-- coding-policy:claude-quality-review:v1 -->'
PASSING_CHECKS='[{"bucket":"pass","name":"unit-tests"},{"bucket":"pass","name":"review"}]'

watch_with_reviews() {
  printf '%s' "$1" > "$TMPDIR_TEST/reviews.json" \
    || { echo "    FAIL: could not write the reviews fixture under ${TMPDIR_TEST}" >&2; return 2; }
  FAKE_GH_REVIEWS_FILE="$TMPDIR_TEST/reviews.json" watch_with_checks "$PASSING_CHECKS"
}

review_json() { # <id> <login> <state> <minute> <body>
  jq -cn --argjson id "$1" --arg login "$2" --arg state "$3" --arg at "2026-10-20T10:$4:00Z" --arg body "$5" \
    '{id: $id, user: {login: $login, type: "Bot"}, state: $state, submitted_at: $at, body: $body,
      commit_id: "@HEAD@", html_url: "https://github.com/gamussa/coding-policy/pull/42#pullrequestreview-\($id)"}'
}

test_e2e_claude_comment_does_not_clear_codex_block() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' CHANGES_REQUESTED 00 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files.\n\n## Blocking findings (gate the merge)\n- `a.sh:1` — **error-handling** — m'),$(review_json 2 'copilot-pull-request-reviewer[bot]' COMMENTED 01 'Copilot reviewed 3 files'),$(review_json 3 'github-actions[bot]' COMMENTED 02 "${CLAUDE_MARK}"$'\nNo correctness issues found.')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "codex state" "CHANGES_REQUESTED" "$(echo "$out" | jq -r '.reviews.codex.state')" || return 1
  assert_eq "claude lane state" "COMMENTED" "$(echo "$out" | jq -r '.lanes["claude-quality"].state')" || return 1
  assert_eq "claude lane gating" "false" "$(echo "$out" | jq -r '.lanes["claude-quality"].gating')" || return 1
}
run "e2e: a clean Claude-lane comment never clears a Codex block" test_e2e_claude_comment_does_not_clear_codex_block

test_e2e_codex_comment_clears_its_own_block() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' CHANGES_REQUESTED 00 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files. One violation.'),$(review_json 2 'copilot-pull-request-reviewer[bot]' COMMENTED 01 'Copilot reviewed 3 files'),$(review_json 3 'github-actions[bot]' COMMENTED 02 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files. All rules pass.')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "codex state" "COMMENTED" "$(echo "$out" | jq -r '.reviews.codex.state')" || return 1
}
run "e2e: a clean Codex comment clears its own block" test_e2e_codex_comment_clears_its_own_block

test_e2e_unmarked_legacy_codex_review_still_gates() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' CHANGES_REQUESTED 00 $'Policy loaded: 30 rule files. One violation.\n\n## Blocking findings (gate the merge)'),$(review_json 2 'copilot-pull-request-reviewer[bot]' COMMENTED 01 'Copilot reviewed 3 files')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "codex lane state" "CHANGES_REQUESTED" "$(echo "$out" | jq -r '.lanes["codex-policy"].state')" || return 1
}
run "e2e: an unmarked legacy Codex review still classifies as Codex" test_e2e_unmarked_legacy_codex_review_still_gates

# Copilot claims no lane, but its CHANGES_REQUESTED still gates until
# resolved through review (rules/ci-safety.md Never Skip Tests).
test_e2e_copilot_block_gates() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' COMMENTED 00 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files. All rules pass.'),$(review_json 2 'copilot-pull-request-reviewer[bot]' CHANGES_REQUESTED 01 'Copilot found a bug')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "review keys" '["codex"]' "$(echo "$out" | jq -c '.reviews | keys')" || return 1
  assert_eq "lane keys" '["claude-quality","codex-policy"]' "$(echo "$out" | jq -c '.lanes | keys')"
}
run "e2e: a Copilot CHANGES_REQUESTED claims no lane but gates" test_e2e_copilot_block_gates

test_e2e_copilot_comment_is_ready() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' COMMENTED 00 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files. All rules pass.'),$(review_json 2 'copilot-pull-request-reviewer[bot]' COMMENTED 01 'Copilot reviewed 3 files')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")"
}
run "e2e: a Copilot COMMENTED review does not hold ready" test_e2e_copilot_comment_is_ready

# A human CHANGES_REQUESTED gates (rules/ci-safety.md Never Skip Tests), even
# with a clean policy verdict; the e2e path runs it through the real poll.
test_e2e_human_block_gates() {
  local reviews out rc=0
  reviews="[$(review_json 1 'github-actions[bot]' COMMENTED 00 "${CODEX_MARK}"$'\nPolicy loaded: 30 rule files. All rules pass.'),$(review_json 2 'alice' CHANGES_REQUESTED 01 'please rename' | jq -c '.user.type = "User"')]"
  out=$(watch_with_reviews "$reviews") || rc=$?
  assert_watch_rc "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")" || return 1
  assert_eq "human review" '[{"login":"alice","is_bot":false,"state":"CHANGES_REQUESTED"}]' \
    "$(echo "$out" | jq -c '.other_reviews | map({login, is_bot, state})')"
}
run "e2e: a human CHANGES_REQUESTED gates" test_e2e_human_block_gates

# A lane flagged gating in the snapshot gates; the same verdict on an advisory
# lane does not. This is the switch ADR 0010's promotion flips.
lanes_snap() { # <claude-state> <claude-gating> [claude-expected]
  snap MERGEABLE CLEAN success COMMENTED | jq -c --arg s "$1" --argjson g "$2" --argjson e "${3:-false}" \
    '. + {lanes: {"codex-policy": (.reviews.codex + {gating: true, expected: true}),
                  "claude-quality": {state: $s, submitted_at: null, body: null, commit_id: null, stale: false, gating: $g, expected: $e}}}'
}

test_advisory_claude_block_does_not_gate() {
  reset_mocks
  queue "$(lanes_snap CHANGES_REQUESTED false)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")"
}
run "an advisory Claude-lane CHANGES_REQUESTED does not gate" test_advisory_claude_block_does_not_gate

# An advisory lane that never posts (a repo without the Claude reviewer) does
# not hold the watch to budget.
test_advisory_lane_absent_is_ready() {
  reset_mocks
  queue "$(lanes_snap none false)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "1" "$(calls)"
}
run "an advisory lane with no verdict does not hold ready" test_advisory_lane_absent_is_ready

test_gating_claude_block_gates() {
  reset_mocks
  queue "$(lanes_snap CHANGES_REQUESTED true)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "changes_requested" "$(result_of "$out")"
}
run "a gating Claude-lane CHANGES_REQUESTED gates" test_gating_claude_block_gates

test_gating_lane_absent_is_not_ready() {
  reset_mocks
  queue "$(lanes_snap none true)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")"
}
run "a gating lane with no verdict is not ready" test_gating_lane_absent_is_not_ready

# An advisory lane whose workflow ran on head owes a verdict: the watcher
# waits for it and never reaches ready without it.
test_expected_advisory_lane_absent_is_not_ready() {
  reset_mocks
  queue "$(lanes_snap none false true)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "result" "pending_at_budget" "$(result_of "$out")"
}
run "an expected advisory lane with no verdict is not ready" test_expected_advisory_lane_absent_is_not_ready

test_expected_advisory_lane_posted_is_ready() {
  reset_mocks
  queue "$(lanes_snap none false true)"
  queue "$(lanes_snap COMMENTED false true)"
  local out rc=0
  out=$(main gamussa coding-policy 42 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "result" "ready" "$(result_of "$out")" || return 1
  assert_eq "poll count" "2" "$(calls)"
}
run "an expected advisory lane is waited for, then ready" test_expected_advisory_lane_posted_is_ready

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
echo "test-summary: passed=${PASS_COUNT} failed=${FAIL_COUNT} token=${SUMMARY_TOKEN}"
exit "$FAIL_COUNT"
