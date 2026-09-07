#!/usr/bin/env bash
# Outcome-based tests for verify-moderation-cleared.sh.
#
# Covers the behaviors the script promises:
#   1. Cleared immediately — first `tessl api` returns moderationStatus
#      "pass"; script emits {"ok":true,...}, exits 0, never sleeps.
#   2. Cleared via the boolean — moderationPassed true (status absent/other)
#      also counts as cleared.
#   3. Deferred clear — first calls return "pending", a later call returns
#      "pass"; script polls (sleeps), then exits 0.
#   4. Blocked by status — a terminal block state (e.g. "flagged") exits 1
#      immediately (fail loud), without waiting out the budget.
#   5. Blocked by moderationError — a non-null error exits 1 immediately.
#   6. Budget exhausted — every call "pending"; script exits 1 with a
#      budget diagnostic and bounded total sleep.
#   7. Arg-count + empty-arg validation — exit 2 with usage/diagnostic.
#   8. Env-var validation — non-positive / inverted bounds exit 2.
#   9. Persistent tessl failure — non-zero `tessl api` is retried up to
#      FETCH_RETRY_MAX consecutive times, then exits 2 (issue #304).
#   9b. Transient then success — a fetch failure below the cap retries in the
#       loop and clears on a later call, exit 0.
#   9c. Retry cap is FETCH_RETRY_MAX — overriding it bounds the retries.
#   9d. Persistent failure to budget — a high cap lets the budget stop the
#       retries; still exit 2 (tool failure), never rc 1.
#  10. Unparseable body — no moderation fields exits 2.
#  15. Subprocess run — the script spawned as its own process, so its
#      `set -euo pipefail` is live, polls the whole budget and emits the
#      JSON envelope (issue #306); every sourced case runs with `set +e`,
#      the exact condition that hid a backoff abort.
#
# Approach mirrors test_resolve_publish_run.sh: source the script (the
# main() guard prevents auto-run when sourced) and override `tessl` and
# `sleep` as shell functions. `main` runs the tessl call inside a command
# substitution (`body=$(...)`), so per-call state lives in tempfiles, not
# shell vars — a counter file the mock increments and a queue directory it
# indexes into (file `N` = the Nth call's response; missing N falls back to
# the highest-numbered file so "always pending" needs a single fixture).
#
# Run: bash skills/release/tests/test_verify_moderation_cleared.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/verify-moderation-cleared.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: verify-moderation-cleared.sh not executable at $SCRIPT" >&2; exit 2; }

# Shrink the backoff so the budget-exhausted test stays fast. Exit codes
# and call counts are what's asserted, not wall-clock timing.
export VERIFY_MODERATION_BASE_DELAY_SEC=1
export VERIFY_MODERATION_MAX_DELAY_SEC=2
export VERIFY_MODERATION_BUDGET_SEC=4

# shellcheck disable=SC1090
source "$SCRIPT"
set +e

FAIL_COUNT=0
PASS_COUNT=0

TMPDIR_TEST=$(mktemp -d -t verify-mod-test.XXXXXX)
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
export MOCK_COUNT_FILE="$TMPDIR_TEST/count"
export MOCK_CALLS_FILE="$TMPDIR_TEST/calls"
export MOCK_SLEEP_FILE="$TMPDIR_TEST/sleeps"
export MOCK_QUEUE_DIR="$TMPDIR_TEST/queue"

# Mocks. A response file beginning with __FAIL__ makes `tessl` echo the
# rest to stderr and return non-zero (simulates a registry/tool failure).
tessl() {
  local n resp
  # Absent count file is the expected first-call state (=> 0); a present but
  # unreadable file is a harness fault that must not read as 0
  # (rules/error-handling.md — expected non-result vs tool failure).
  if [[ -f "$MOCK_COUNT_FILE" ]]; then
    n=$(cat "$MOCK_COUNT_FILE") || { echo "test harness: cannot read $MOCK_COUNT_FILE" >&2; exit 2; }
  else
    n=0
  fi
  n=$(( n + 1 ))
  echo "$n" > "$MOCK_COUNT_FILE"
  echo "tessl $*" >> "$MOCK_CALLS_FILE"
  if [[ -f "$MOCK_QUEUE_DIR/$n" ]]; then
    resp="$MOCK_QUEUE_DIR/$n"
  else
    # Sticky last: highest-numbered fixture file. queue() names files by
    # integer call number, so a numeric max over the basenames (glob, no
    # `ls` parsing) picks the latest queued response.
    local highest=0 f
    resp=""
    for f in "$MOCK_QUEUE_DIR"/*; do
      [[ -f "$f" ]] || continue
      if (( ${f##*/} > highest )); then highest=${f##*/}; resp="$f"; fi
    done
  fi
  [[ -z "$resp" || ! -f "$resp" ]] && { echo "mock: no queued response for call $n" >&2; return 1; }
  if [[ "$(head -1 "$resp")" == "__FAIL__" ]]; then
    tail -n +2 "$resp" >&2
    return 1
  fi
  cat "$resp"
}
sleep() { echo "$*" >> "$MOCK_SLEEP_FILE"; }
# The suite sources the script rather than spawning it, so the export is
# belt-and-braces. Warn rather than suppress: silently losing it would
# surface as a mock that never fires, which reads as a script bug
# (rules/error-handling.md Shell Error Handling).
if ! export -f tessl sleep 2>/dev/null; then
  echo "warning: 'export -f' unavailable — mocks apply to this shell only" >&2
fi

reset_mocks() {
  rm -rf "$MOCK_QUEUE_DIR"; mkdir -p "$MOCK_QUEUE_DIR"
  : > "$MOCK_CALLS_FILE"; : > "$MOCK_SLEEP_FILE"; echo 0 > "$MOCK_COUNT_FILE"
}

# Build a moderation JSON body. $1=status (empty to omit), $2=passed
# (empty to omit), $3=error (empty => null).
body_json() {
  local status="$1" passed="$2" err="$3"
  local attrs=""
  [[ -n "$status" ]] && attrs="\"moderationStatus\":\"$status\""
  [[ -n "$passed" ]] && attrs="${attrs:+$attrs,}\"moderationPassed\":$passed"
  if [[ -n "$err" ]]; then attrs="${attrs:+$attrs,}\"moderationError\":\"$err\""; else attrs="${attrs:+$attrs,}\"moderationError\":null"; fi
  printf '{"data":{"attributes":{%s}}}' "$attrs"
}

queue() { printf '%s' "$2" > "$MOCK_QUEUE_DIR/$1"; }

count_calls() { wc -l < "$MOCK_CALLS_FILE" | tr -d ' '; }
count_sleeps() { [[ -s "$MOCK_SLEEP_FILE" ]] || { echo 0; return; }; wc -l < "$MOCK_SLEEP_FILE" | tr -d ' '; }

pass() { PASS_COUNT=$(( PASS_COUNT + 1 )); echo "  PASS: $1"; }
fail() { FAIL_COUNT=$(( FAIL_COUNT + 1 )); echo "  FAIL: $1" >&2; }

# --- 1. Cleared immediately ---
reset_mocks
queue 1 "$(body_json pass true '')"
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 0 ]] && echo "$out" | jq -e '.ok == true' >/dev/null 2>&1 && [[ "$(count_sleeps)" == "0" ]]; then
  pass "cleared immediately: exit 0, ok=true, no sleep"
else
  fail "cleared immediately (rc=$rc out=$out sleeps=$(count_sleeps))"
fi

# --- 2. Cleared via boolean only ---
reset_mocks
queue 1 "$(body_json '' true '')"
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 0 ]] && echo "$out" | jq -e '.ok == true' >/dev/null 2>&1; then
  pass "cleared via moderationPassed=true with no status"
else
  fail "cleared via boolean (rc=$rc out=$out)"
fi

# --- 3. Deferred clear ---
reset_mocks
queue 1 "$(body_json pending '' '')"
queue 2 "$(body_json pending '' '')"
queue 3 "$(body_json pass true '')"
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 0 ]] && echo "$out" | jq -e '.ok == true and .attempts == 3' >/dev/null 2>&1 && [[ "$(count_sleeps)" -ge 2 ]]; then
  pass "deferred clear: polled to pass on 3rd check, exit 0"
else
  fail "deferred clear (rc=$rc out=$out calls=$(count_calls) sleeps=$(count_sleeps))"
fi

# --- 4. Blocked by status (fail loud, immediate) ---
reset_mocks
queue 1 "$(body_json flagged false '')"
queue 2 "$(body_json pass true '')"   # would clear if it kept polling; it must NOT
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 1 ]] && echo "$out" | jq -e '.ok == false' >/dev/null 2>&1 && [[ "$(count_calls)" == "1" ]] && [[ "$(count_sleeps)" == "0" ]]; then
  pass "blocked status: exit 1 immediately, no further polling"
else
  fail "blocked status (rc=$rc out=$out calls=$(count_calls))"
fi

# --- 5. Blocked by moderationError ---
reset_mocks
queue 1 "$(body_json pending '' 'policy violation: embedded credentials')"
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 1 ]] && echo "$out" | jq -e '.ok == false' >/dev/null 2>&1 && [[ "$(count_calls)" == "1" ]]; then
  pass "blocked by non-null moderationError: exit 1 immediately"
else
  fail "blocked by error (rc=$rc out=$out calls=$(count_calls))"
fi

# --- 5b. Blocked by moderationError ALONE (no status, no passed) ---
# Regression guard: the parse guard must treat moderationError as a
# parsed field, so an error-only body is the rc 1 blocked finding, not
# an rc 2 "could not parse" tool error.
reset_mocks
queue 1 '{"data":{"attributes":{"moderationError":"policy violation"}}}'
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 1 ]] && echo "$out" | jq -e '.ok == false' >/dev/null 2>&1; then
  pass "blocked by moderationError alone: exit 1 (not exit 2 parse error)"
else
  fail "error-only body (rc=$rc out=$out)"
fi

# --- 6. Budget exhausted ---
reset_mocks
queue 1 "$(body_json pending '' '')"   # sticky-last => every call is pending
out=$(main acme widget 1.2.3); rc=$?
if [[ $rc -eq 1 ]] && echo "$out" | jq -e '.ok == false' >/dev/null 2>&1 && echo "$out" | grep -q "budget"; then
  pass "budget exhausted: exit 1 with budget diagnostic"
else
  fail "budget exhausted (rc=$rc out=$out)"
fi

# --- 7. Arg validation ---
# Wrap in subshells: main's `exit` would otherwise terminate this harness
# when not run inside a command substitution.
reset_mocks
( main acme widget ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "missing arg: exit 2"; else fail "missing arg (rc=$rc)"; fi
( main acme widget "" ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "empty version arg: exit 2"; else fail "empty version (rc=$rc)"; fi

# --- 8. Env-var validation ---
# main() reads the resolved globals (BASE_DELAY_SEC, MAX_DELAY_SEC,
# BUDGET_SEC) set once at source time — it does NOT re-read the
# VERIFY_MODERATION_* env vars per call. So the override must set those
# globals, command-scoped to the main() call (a bash env-prefix on a
# function makes the var visible inside it and restores it after). The
# subshell only isolates main's `exit`.
reset_mocks
( BASE_DELAY_SEC=0 main acme widget 1.2.3 ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "base delay 0: exit 2"; else fail "base delay 0 (rc=$rc)"; fi
( BASE_DELAY_SEC=10 MAX_DELAY_SEC=5 main acme widget 1.2.3 ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "base > max: exit 2"; else fail "base > max (rc=$rc)"; fi
( BASE_DELAY_SEC=100 MAX_DELAY_SEC=100 BUDGET_SEC=10 main acme widget 1.2.3 ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "base > budget: exit 2"; else fail "base > budget (rc=$rc)"; fi
( FETCH_RETRY_MAX=0 main acme widget 1.2.3 ) >/dev/null 2>&1; rc=$?
if [[ $rc -eq 2 ]]; then pass "fetch-retry-max 0: exit 2"; else fail "fetch-retry-max 0 (rc=$rc)"; fi

# --- 9. Persistent tessl failure: retried FETCH_RETRY_MAX times, then exit 2 ---
# A single fixture is sticky-last, so every call fails. Default
# FETCH_RETRY_MAX=3 => 3 consecutive failures before the hard exit 2.
reset_mocks
queue 1 "$(printf '__FAIL__\n500 Internal Server Error')"
out=$(main acme widget 1.2.3 2>/dev/null); rc=$?
if [[ $rc -eq 2 ]] && [[ "$(count_calls)" == "3" ]]; then
  pass "persistent tessl failure: retried to FETCH_RETRY_MAX then exit 2"
else
  fail "persistent tessl failure (rc=$rc calls=$(count_calls) out=$out)"
fi

# --- 9b. Transient failure then success: retry inside the loop, exit 0 ---
# First call fails (below FETCH_RETRY_MAX), the next clears (issue #304).
reset_mocks
queue 1 "$(printf '__FAIL__\n502 Bad Gateway')"
queue 2 "$(body_json pass true '')"
out=$(main acme widget 1.2.3 2>/dev/null); rc=$?
if [[ $rc -eq 0 ]] && echo "$out" | jq -e '.ok == true' >/dev/null 2>&1 && [[ "$(count_calls)" == "2" ]] && [[ "$(count_sleeps)" -ge 1 ]]; then
  pass "transient failure then pass: retried and cleared, exit 0"
else
  fail "transient then pass (rc=$rc out=$out calls=$(count_calls) sleeps=$(count_sleeps))"
fi

# --- 9c. Retry cap is FETCH_RETRY_MAX ---
# Command-scope the global (same pattern as the env-var tests): 2 consecutive
# failures now exit 2, proving the cap is the constant, not hardcoded.
reset_mocks
queue 1 "$(printf '__FAIL__\n500 err')"
out=$( FETCH_RETRY_MAX=2 main acme widget 1.2.3 2>/dev/null ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$(count_calls)" == "2" ]]; then
  pass "FETCH_RETRY_MAX=2: exit 2 after exactly 2 failures"
else
  fail "retry cap override (rc=$rc calls=$(count_calls))"
fi

# --- 9d. Persistent failure hits the budget before a high retry cap ---
# With a high FETCH_RETRY_MAX the budget bound stops the retries; it exits 2
# (tool failure), never rc 1 (rc 1 is a moderation verdict, not a tool fault).
reset_mocks
queue 1 "$(printf '__FAIL__\n503 err')"
err=$( { FETCH_RETRY_MAX=100 main acme widget 1.2.3 >/dev/null; } 2>&1 ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$err" == *"budget is exhausted"* ]]; then
  pass "persistent failure to budget: exit 2 with budget diagnostic"
else
  fail "transient-to-budget (rc=$rc err=$err)"
fi

# --- 10. Unparseable body ---
reset_mocks
queue 1 '{"data":{"attributes":{}}}'
out=$(main acme widget 1.2.3 2>/dev/null); rc=$?
if [[ $rc -eq 2 ]]; then pass "no moderation fields: exit 2"; else fail "unparseable (rc=$rc out=$out)"; fi

# --- 11. Body that is not JSON at all ---
# The field extractions used to carry `2>/dev/null || true`, which read a
# non-JSON body (a proxy error page, an HTML 502) as "no moderation fields
# present". The script still exited 2 — this was never a vacuous pass — but
# it reported the wrong cause: "the registry response shape may have
# changed; update verify-moderation-cleared.sh's jq paths", sending the
# operator to edit a parse that is working fine against a registry that is
# returning 502s. Per rules/error-handling.md Actionable Messages, the
# message must name what to do; naming the wrong thing is worse than
# terse, because it is confidently wrong.
reset_mocks
queue 1 '<html><head><title>502 Bad Gateway</title></head></html>'
err=$( { main acme widget 1.2.3 >/dev/null; } 2>&1 ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$err" == *"not valid JSON"* ]]; then
  pass "non-JSON body: exit 2 naming the real fault"
else
  fail "non-JSON body (rc=$rc err=$err)"
fi

# --- 12. Truncated JSON body ---
# jq fails outright here rather than returning empty; the old suppression
# turned that failure into the same misdirected "update the jq paths"
# message as case 11.
reset_mocks
queue 1 '{"data":{"attributes":{"moderationStatus":'
err=$( { main acme widget 1.2.3 >/dev/null; } 2>&1 ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$err" == *"not valid JSON"* ]]; then
  pass "truncated JSON body: exit 2 naming the real fault"
else
  fail "truncated JSON (rc=$rc err=$err)"
fi

# --- 13/14. Valid JSON, WRONG SHAPE — must reach the shape-changed
# diagnostic, not crash on jq's raw index error. `.data` a string, and
# `.data.attributes` a string (nested), are the two levels a one-step
# `.attributes?` guard misses; only `(.data.attributes.FIELD)?` catches both.
reset_mocks
queue 1 '{"data":"a-string-not-an-object"}'
err=$( { main acme widget 1.2.3 >/dev/null; } 2>&1 ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$err" == *"response shape may have changed"* ]]; then
  pass "wrong shape (.data string): shape-changed diagnostic, not a jq crash"
else
  fail "wrong shape .data string (rc=$rc err=$err)"
fi

reset_mocks
queue 1 '{"data":{"attributes":"not-an-object"}}'
err=$( { main acme widget 1.2.3 >/dev/null; } 2>&1 ); rc=$?
if [[ $rc -eq 2 ]] && [[ "$err" == *"response shape may have changed"* ]]; then
  pass "wrong shape (.data.attributes string): shape-changed diagnostic"
else
  fail "wrong shape .attributes string (rc=$rc err=$err)"
fi

# --- 15. Subprocess run: backoff under the script's own `set -euo pipefail` ---
# Every case above SOURCES the script and then runs `set +e`, which is exactly
# the condition that hid issue #306: `advance_backoff` ended with a
# `(( ... )) && delay=...` list whose false status became the function's return
# status, and under a live `-e` that killed the script at the FIRST backoff —
# one poll, empty stdout, rc 1, the code the contract reserves for a real
# moderation finding. Spawn the script so its own `-e` is live and assert the
# full schedule ran: base 1, cap 8 => sleeps 1,2,4,8 with the 5th check
# stopping because another 8 would pass the 20s budget => attempts 5,
# elapsed 15, and a parseable envelope on stdout.
STUB_BIN="$TMPDIR_TEST/bin"
if ! mkdir -p "$STUB_BIN"; then
  echo "fatal: cannot create stub bin dir $STUB_BIN" >&2; exit 2
fi
# An always-pending registry and a no-op sleep, as real executables: a
# subprocess resolves `tessl`/`sleep` through PATH. The assertions read the
# script's OWN attempt/elapsed counters, so they hold whether the child picks
# up these stubs or the exported mock functions above.
cat > "$STUB_BIN/tessl" <<'STUB'
#!/usr/bin/env bash
printf '{"data":{"attributes":{"moderationStatus":"pending","moderationError":null}}}'
STUB
cat > "$STUB_BIN/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
if ! chmod +x "$STUB_BIN/tessl" "$STUB_BIN/sleep"; then
  echo "fatal: cannot make the stubs in $STUB_BIN executable" >&2; exit 2
fi
reset_mocks
queue 1 "$(body_json pending '' '')"
out=$( PATH="$STUB_BIN:$PATH" \
       VERIFY_MODERATION_BASE_DELAY_SEC=1 \
       VERIFY_MODERATION_MAX_DELAY_SEC=8 \
       VERIFY_MODERATION_BUDGET_SEC=20 \
       bash "$SCRIPT" acme widget 1.2.3 2>/dev/null ); rc=$?
if [[ $rc -eq 1 ]] && echo "$out" | jq -e '.ok == false and .attempts == 5 and .elapsed_seconds == 15' >/dev/null 2>&1; then
  pass "subprocess run: polls the whole budget, emits the JSON envelope on rc 1"
else
  fail "subprocess run (rc=$rc out=$out)"
fi

echo
echo "verify-moderation-cleared: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]]
