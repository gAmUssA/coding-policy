#!/usr/bin/env bash
# Outcome-based tests for resolve-publish-run.sh.
#
# Covers behaviors the script promises:
#   1. Immediate hit on the default `main` ref — the four-argument form
#      still emits {"database_id": N} and exits 0 without sleeping.
#   2. Explicit tag ref — a tag-triggered run is selected through the
#      same path (the tag-release gap in coding-policy#371).
#   3. Ref binding — an unrelated branch or tag at the same commit is
#      never selected, and its id never reaches stdout.
#   4. Event binding — a `workflow_dispatch` run on the requested ref
#      and commit is excluded, and the ref predicate still holds when
#      the transport serves an unnarrowed listing.
#   5. Delayed enqueue — first calls return an empty listing, a later
#      one carries the run; the script polls and then emits it.
#   6. Ambiguity — two runs matching workflow + commit + event + ref
#      exit non-zero with a diagnostic naming both, and emit no id.
#   7. Budget exhausted — the listing never carries a match; exit
#      non-zero with a diagnostic naming the commit, ref and workflow.
#   8. Arg-count validation — 3 args and 6 args produce exit 2 + usage.
#   9. Env-var validation — non-positive-integer INTERVAL/BUDGET values
#      produce exit 2 with a clear diagnostic naming the bad var.
#  10. INTERVAL > BUDGET rejected.
#  11. Budget cap — total sleep never exceeds BUDGET_SEC even when
#      INTERVAL doesn't divide BUDGET evenly.
#  12. Numeric run-id validation — a listing whose databaseId is not a
#      number produces exit 1 with an actionable diagnostic.
#
# Approach: source the script (the main() guard prevents auto-run when
# sourced) and override `gh` + `sleep` as shell functions. The `gh`
# override is a TRANSPORT mock, not a result mock: each queued response
# names a fixture file holding a raw `gh run list --json` array, and the
# mock reproduces what real gh does with it — narrow server-side on
# `--branch`, then run the caller's `--jq` filter through jq. So the
# script's own selection predicate is what these tests exercise; a
# filter that stopped binding the ref or the event would red here.
#
# Because `main` runs the gh call inside a command substitution
# (`matches=$(...)`), state like "which call is this" can't live in
# shell variables — the substitution spawns a subshell that gets its own
# copy and writes don't propagate back. State lives in tempfiles
# instead: a calls log the mock appends to, an args log for asserting
# what was requested, and a queue file the mock indexes into per call.
#
# Run: bash skills/release/tests/test_resolve_publish_run.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/resolve-publish-run.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: resolve-publish-run.sh not executable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq is required — the transport mock filters fixtures with it" >&2; exit 2; }

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

# Fixed fixture values. Every date-free, RNG-free literal here is
# checked into the test itself (rules/testing-standards.md Determinism).
SHA_RELEASE=723c427ca75cbb226a5edf76dc903a5243f1d8b4
SHA_MERGE=aa11bb22cc33dd44ee55ff6607788990aabbccdd
TAG=v0.1.4
WORKFLOW=publish.yml

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
export MOCK_GH_ARGS_FILE="$TMPDIR_TEST/gh-args"
export MOCK_SLEEP_CALLS_FILE="$TMPDIR_TEST/sleep-calls"
export MOCK_GH_QUEUE_FILE="$TMPDIR_TEST/gh-queue"
export FIXTURE_DIR="$TMPDIR_TEST/fixtures"
mkdir -p "$FIXTURE_DIR"

# --- Transport fixtures: raw `gh run list --json ...` arrays -----------------
# Written in setup rather than checked in, per rules/testing-standards.md
# Fixtures ("build test data programmatically in test setup").
write_fixture() { cat > "$FIXTURE_DIR/$1"; }

write_fixture empty.json <<JSON
[]
JSON

# A merge to main: the publish run plus an older unrelated run.
write_fixture main-push.json <<JSON
[
  {"databaseId": 34288062324, "headSha": "${SHA_MERGE}", "event": "push", "headBranch": "main"},
  {"databaseId": 34200000001, "headSha": "0000000000000000000000000000000000000000", "event": "push", "headBranch": "main"}
]
JSON

# A tag release. The same commit also carries a run on the branch the
# tag was cut from, and a manual dispatch on the tag itself. Only the
# tag's push run may be selected.
write_fixture tag-push.json <<JSON
[
  {"databaseId": 34188269042, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "${TAG}"},
  {"databaseId": 34188100000, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "main"},
  {"databaseId": 34188200000, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "release/0.1.x"},
  {"databaseId": 34188300000, "headSha": "${SHA_RELEASE}", "event": "workflow_dispatch", "headBranch": "${TAG}"}
]
JSON

# The same commit on other refs only — nothing on the requested tag.
write_fixture unrelated-refs-only.json <<JSON
[
  {"databaseId": 34188100000, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "main"},
  {"databaseId": 34188200000, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "release/0.1.x"}
]
JSON

# The requested ref and commit, reached by a manual dispatch only.
write_fixture dispatch-only.json <<JSON
[
  {"databaseId": 34188300000, "headSha": "${SHA_RELEASE}", "event": "workflow_dispatch", "headBranch": "${TAG}"}
]
JSON

# A deleted-and-re-pushed tag: two push runs on the same ref and commit.
write_fixture ambiguous-tag.json <<JSON
[
  {"databaseId": 34188269042, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "${TAG}"},
  {"databaseId": 34199999999, "headSha": "${SHA_RELEASE}", "event": "push", "headBranch": "${TAG}"}
]
JSON

write_fixture non-numeric-id.json <<JSON
[
  {"databaseId": "not-a-number", "headSha": "${SHA_MERGE}", "event": "push", "headBranch": "main"}
]
JSON

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

assert_absent() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: '${needle}' should not appear in: ${haystack}" >&2
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

# Mock `gh` — a transport stand-in for `gh run list ... --jq '...'`.
# Reproduces real gh's two-stage behavior: `--branch` narrows the
# listing server-side, then `--jq` runs over what survives. Records each
# invocation's arguments so tests can assert the ref actually requested.
gh() {
  [[ "$1" == "run" && "$2" == "list" ]] || { echo "mock gh: unexpected invocation: $*" >&2; return 99; }
  echo "call" >> "$MOCK_GH_CALLS_FILE"
  echo "$*" >> "$MOCK_GH_ARGS_FILE"

  local branch="" workflow="" filter="" prev="" arg
  for arg in "$@"; do
    case "$prev" in
      --branch) branch="$arg" ;;
      --workflow) workflow="$arg" ;;
      --jq) filter="$arg" ;;
    esac
    prev="$arg"
  done
  [[ -n "$filter" ]] || { echo "mock gh: no --jq filter passed" >&2; return 99; }

  local call_count fixture payload
  call_count=$(wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' ')
  fixture=$(sed -n "${call_count}p" "$MOCK_GH_QUEUE_FILE")
  [[ -n "$fixture" ]] || fixture=empty.json
  [[ -f "$FIXTURE_DIR/$fixture" ]] || { echo "mock gh: no fixture named ${fixture}" >&2; return 99; }

  # Server-side `--branch` narrowing, as the Actions API applies it.
  # MOCK_GH_IGNORE_BRANCH=1 serves the unnarrowed listing instead, so a
  # test can exercise the script's own client-side ref predicate rather
  # than the transport's.
  payload=$(jq -c --arg w "$workflow" --arg expected "$WORKFLOW" '[.[] | select((.workflowName // $expected) == $w)]' "$FIXTURE_DIR/$fixture") || return 99
  if [[ "${MOCK_GH_IGNORE_BRANCH:-0}" != "1" ]]; then
    payload=$(jq -c --arg b "$branch" '[ .[] | select(.headBranch == $b) ]' <<<"$payload") || return 99
  fi
  jq -r "$filter" <<<"$payload"
}

# Mock `sleep` — record the requested duration (one per line) without
# actually waiting. Tests sum these to verify the loop respects the
# wall-clock budget.
sleep() {
  echo "$1" >> "$MOCK_SLEEP_CALLS_FILE"
}

reset_mocks() {
  : > "$MOCK_GH_CALLS_FILE"
  : > "$MOCK_GH_ARGS_FILE"
  : > "$MOCK_SLEEP_CALLS_FILE"
  : > "$MOCK_GH_QUEUE_FILE"
  export MOCK_GH_IGNORE_BRANCH=0
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

queue_fixtures() {
  for r in "$@"; do
    echo "$r" >> "$MOCK_GH_QUEUE_FILE"
  done
}

gh_calls() { wc -l < "$MOCK_GH_CALLS_FILE" | tr -d ' '; }
gh_args() { cat "$MOCK_GH_ARGS_FILE"; }
sleep_calls() { wc -l < "$MOCK_SLEEP_CALLS_FILE" | tr -d ' '; }
total_sleep_seconds() { awk '{ sum += $1 } END { print sum + 0 }' "$MOCK_SLEEP_CALLS_FILE"; }

# Extract .database_id from a JSON envelope; prints empty if absent.
database_id_of() { echo "$1" | jq -r '.database_id // empty'; }

# --- Test 1: immediate hit on the default main ref ---------------------------
test_main_push_default_ref() {
  reset_mocks
  queue_fixtures main-push.json
  local output rc=0
  output=$(main jbaruch coding-policy "$SHA_MERGE" "$WORKFLOW" 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "34288062324" "$(database_id_of "$output")" || return 1
  assert_eq "gh call count" "1" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "0" "$(sleep_calls)" || return 1
  gh_args | grep -q -- "--branch main" || { echo "    FAIL: default ref should request --branch main, got: $(gh_args)" >&2; return 1; }
}
run "four-arg main push resolves without sleeping" test_main_push_default_ref

# --- Test 2: explicit tag ref -------------------------------------------------
test_explicit_tag_ref() {
  reset_mocks
  queue_fixtures tag-push.json
  local output rc=0
  output=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "34188269042" "$(database_id_of "$output")" || return 1
  gh_args | grep -q -- "--branch ${TAG}" || { echo "    FAIL: should request --branch ${TAG}, got: $(gh_args)" >&2; return 1; }
}
run "explicit tag ref resolves the tag's push run" test_explicit_tag_ref

# Quoted refs are valid Git names, and query-looking characters remain
# literal data. Run the actual helper filter against raw transport bodies.
test_quoted_ref_binding() {
  local ref output rc mode
  for ref in 'v1.0"quoted' 'v")|select(.event=="workflow_dispatch")#'; do
    git check-ref-format "refs/tags/$ref" || return 1
    jq -n --arg ref "$ref" --arg sha "$SHA_RELEASE" '[
      {databaseId: 34188269042, headSha: $sha, event: "push", headBranch: $ref},
      {databaseId: 34188300000, headSha: $sha, event: "workflow_dispatch", headBranch: $ref},
      {databaseId: 34188100000, headSha: $sha, event: "push", headBranch: "main"},
      {databaseId: 34188400000, headSha: $sha, event: "push", headBranch: "v1.0quoted"},
      {databaseId: 34188500000, headSha: "other-commit", event: "push", headBranch: $ref},
      {databaseId: 34188600000, headSha: $sha, event: "push", headBranch: $ref, workflowName: "unrelated.yml"}
    ]' > "$FIXTURE_DIR/quoted-ref.json" || return 1
    for mode in 0 1; do
      reset_mocks
      export MOCK_GH_IGNORE_BRANCH="$mode"
      queue_fixtures quoted-ref.json
      rc=0
      output=$(main jbaruch coding-policy "$SHA_RELEASE" "$WORKFLOW" "$ref" 2>"$TMPDIR_TEST/err") || rc=$?
      assert_eq "quoted-ref exit code" "0" "$rc" || { cat "$TMPDIR_TEST/err" >&2; return 1; }
      assert_eq "only the exact quoted-ref push resolves" "34188269042" "$(database_id_of "$output")" || return 1
    done
    # Neither stripping a quote nor executing the query-shaped tag may
    # select a run when that exact ref has no push at the requested SHA.
    jq '[.[] | select(.databaseId != 34188269042)]' "$FIXTURE_DIR/quoted-ref.json" > "$FIXTURE_DIR/quoted-ref-absent.json" || return 1
    reset_mocks
    export MOCK_GH_IGNORE_BRANCH=1
    queue_fixtures quoted-ref-absent.json quoted-ref-absent.json quoted-ref-absent.json quoted-ref-absent.json
    rc=0
    output=$(main jbaruch coding-policy "$SHA_RELEASE" "$WORKFLOW" "$ref" 2>"$TMPDIR_TEST/err") || rc=$?
    assert_eq "missing quoted-ref run exits 1" "1" "$rc" || return 1
    assert_eq "missing quoted-ref run emits no id" "" "$output" || return 1
  done
}
run "quoted and query-shaped refs retain exact commit/event/ref binding" test_quoted_ref_binding

# The transport can use jq internally while the real CLI entry point has
# no jq binary on PATH, just as gh supplies its own query engine.
test_cli_quoted_ref_without_system_jq() {
  local bin="$TMPDIR_TEST/no-jq" ref='v1.0"quoted' output rc=0 tool
  mkdir "$bin" || return 1
  for tool in wc tr; do
    ln -s "$(command -v "$tool")" "$bin/$tool" || return 1
  done
  cat > "$bin/gh" <<'MOCK'
#!/bin/bash
set -euo pipefail
filter=""
while (( $# )); do
  if [[ "$1" == --jq ]]; then filter="$2"; shift; fi
  shift
done
"$MOCK_REAL_JQ" -r "$filter" "$FIXTURE_DIR/quoted-cli.json"
MOCK
  chmod +x "$bin/gh" || return 1
  jq -n --arg ref "$ref" --arg sha "$SHA_RELEASE" '[{databaseId: 34188269042, headSha: $sha, event: "push", headBranch: $ref}]' > "$FIXTURE_DIR/quoted-cli.json" || return 1
  output=$(PATH="$bin" MOCK_REAL_JQ="$(command -v jq)" "$BASH" "$SCRIPT" jbaruch coding-policy "$SHA_RELEASE" "$WORKFLOW" "$ref" 2>"$TMPDIR_TEST/err") || rc=$?
  assert_eq "CLI exit code without system jq" "0" "$rc" || { cat "$TMPDIR_TEST/err" >&2; return 1; }
  assert_eq "CLI quoted ref resolves" "34188269042" "$(database_id_of "$output")" || return 1
}
run "CLI resolves a quoted ref with no system jq on PATH" test_cli_quoted_ref_without_system_jq

# --- Test 3: unrelated refs at the same commit are excluded -------------------
test_unrelated_same_sha_refs_excluded() {
  reset_mocks
  queue_fixtures tag-push.json
  local output rc=0
  output=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_absent "main-branch run id" "34188100000" "$output" || return 1
  assert_absent "release-branch run id" "34188200000" "$output" || return 1
}
run "unrelated branches at the same commit are never selected" test_unrelated_same_sha_refs_excluded

test_ref_with_no_run_is_not_found() {
  reset_mocks
  queue_fixtures unrelated-refs-only.json unrelated-refs-only.json unrelated-refs-only.json unrelated-refs-only.json
  local stderr rc=0
  stderr=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>&1 >/dev/null) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected non-zero exit when the requested ref has no run" >&2; return 1; }
  echo "$stderr" | grep -q "$TAG" || { echo "    FAIL: stderr missing the requested ref, got: ${stderr}" >&2; return 1; }
}
run "same commit on other refs only does not resolve" test_ref_with_no_run_is_not_found

# The client-side `headBranch` predicate, exercised on its own: the
# transport serves the whole listing unnarrowed, as it would if gh's
# `--branch` stopped filtering tag refs.
test_client_side_ref_predicate() {
  reset_mocks
  export MOCK_GH_IGNORE_BRANCH=1
  queue_fixtures tag-push.json
  local output rc=0
  output=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "34188269042" "$(database_id_of "$output")" || return 1
}
run "ref binding holds when the transport serves an unnarrowed listing" test_client_side_ref_predicate

# --- Test 4: workflow_dispatch on the requested ref is excluded ---------------
test_manual_dispatch_excluded() {
  reset_mocks
  queue_fixtures dispatch-only.json dispatch-only.json dispatch-only.json dispatch-only.json
  local out stderr rc=0
  out=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>"$TMPDIR_TEST/err") || rc=$?
  stderr=$(cat "$TMPDIR_TEST/err")
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected non-zero exit for a dispatch-only listing" >&2; return 1; }
  assert_absent "dispatch run id" "34188300000" "${out}${stderr}" || return 1
}
run "workflow_dispatch at the requested ref and commit is excluded" test_manual_dispatch_excluded

# --- Test 5: delayed enqueue --------------------------------------------------
test_delayed_enqueue() {
  reset_mocks
  queue_fixtures empty.json empty.json tag-push.json
  local output rc=0
  output=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>&1) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "database_id" "34188269042" "$(database_id_of "$output")" || return 1
  assert_eq "gh call count" "3" "$(gh_calls)" || return 1
  assert_eq "sleep call count" "2" "$(sleep_calls)" || return 1
}
run "delayed enqueue polls until the tag run is listed" test_delayed_enqueue

# --- Test 6: ambiguity refused ------------------------------------------------
test_ambiguous_matches_refused() {
  reset_mocks
  queue_fixtures ambiguous-tag.json
  local out stderr rc=0
  out=$(main jbaruch agentic-context-registry "$SHA_RELEASE" "$WORKFLOW" "$TAG" 2>"$TMPDIR_TEST/err") || rc=$?
  stderr=$(cat "$TMPDIR_TEST/err")
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "stdout must carry no id" "" "$(database_id_of "$out")" || return 1
  echo "$stderr" | grep -q "34188269042" || { echo "    FAIL: diagnostic should name the first candidate, got: ${stderr}" >&2; return 1; }
  echo "$stderr" | grep -q "34199999999" || { echo "    FAIL: diagnostic should name the second candidate, got: ${stderr}" >&2; return 1; }
}
run "two matching runs are refused, not silently narrowed" test_ambiguous_matches_refused

# --- Test 7: budget exhausted -------------------------------------------------
test_budget_exhausted() {
  reset_mocks
  queue_fixtures empty.json empty.json empty.json empty.json empty.json
  local stderr rc=0
  stderr=$(main jbaruch coding-policy "$SHA_MERGE" "$WORKFLOW" 2>&1 >/dev/null) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected non-zero exit, got 0" >&2; return 1; }
  echo "$stderr" | grep -q "$SHA_MERGE" || { echo "    FAIL: stderr missing SHA, got: ${stderr}" >&2; return 1; }
  echo "$stderr" | grep -q "$WORKFLOW" || { echo "    FAIL: stderr missing workflow name, got: ${stderr}" >&2; return 1; }
}
run "budget exhausted exits non-zero with diagnostic" test_budget_exhausted

# --- Test 8: arg count validation ---------------------------------------------
test_too_few_args() {
  reset_mocks
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc123 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "usage:" || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "three args exits 2 with usage" test_too_few_args

test_too_many_args() {
  reset_mocks
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc123 "$WORKFLOW" "$TAG" extra 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "usage:" || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "six args exits 2 with usage" test_too_many_args

test_empty_ref_rejected() {
  reset_mocks
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc123 "$WORKFLOW" "" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "ref must not be empty" || { echo "    FAIL: stderr should name the empty ref, got: ${stderr}" >&2; return 1; }
}
run "empty ref argument rejected" test_empty_ref_rejected

# --- Test 9: env-var validation (positive integer requirement) ----------------
test_interval_zero_rejected() {
  reset_mocks
  INTERVAL_SEC=0
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc "$WORKFLOW" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "INTERVAL_SEC" || { echo "    FAIL: stderr should name INTERVAL_SEC var, got: ${stderr}" >&2; return 1; }
}
run "INTERVAL_SEC=0 rejected with named diagnostic" test_interval_zero_rejected

test_budget_negative_rejected() {
  reset_mocks
  BUDGET_SEC=-5
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc "$WORKFLOW" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "BUDGET_SEC" || { echo "    FAIL: stderr should name BUDGET_SEC var, got: ${stderr}" >&2; return 1; }
}
run "BUDGET_SEC=-5 rejected with named diagnostic" test_budget_negative_rejected

# --- Test 10: INTERVAL > BUDGET rejected --------------------------------------
test_interval_gt_budget_rejected() {
  reset_mocks
  INTERVAL_SEC=10
  BUDGET_SEC=5
  local stderr rc=0
  stderr=$(main jbaruch coding-policy abc "$WORKFLOW" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  echo "$stderr" | grep -q "cannot exceed" || { echo "    FAIL: stderr should explain interval-vs-budget, got: ${stderr}" >&2; return 1; }
}
run "INTERVAL_SEC > BUDGET_SEC rejected" test_interval_gt_budget_rejected

# --- Test 11: budget cap — total sleep cannot exceed BUDGET_SEC --------------
# With INTERVAL=2 and BUDGET=3, a naive `sleep $INTERVAL` after each
# poll would sleep twice (4s total). The script caps the final sleep
# at remaining-budget so total sleep <= BUDGET_SEC.
test_budget_cap_on_non_divisible_interval() {
  reset_mocks
  # shellcheck disable=SC2034  # read by the sourced resolve-publish-run.sh; shellcheck can't trace the source boundary
  INTERVAL_SEC=2
  BUDGET_SEC=3
  queue_fixtures empty.json empty.json empty.json empty.json
  local rc=0
  # Wrap main in a subshell so its `exit 1` on budget exhaustion
  # doesn't kill the test runner.
  ( main jbaruch coding-policy abc "$WORKFLOW" >/dev/null 2>&1 ) || rc=$?
  [[ $rc -ne 0 ]] || { echo "    FAIL: expected budget-exhausted non-zero exit" >&2; return 1; }
  local total
  total=$(total_sleep_seconds)
  [[ "$total" -le "$BUDGET_SEC" ]] || { echo "    FAIL: total sleep ${total}s exceeds budget ${BUDGET_SEC}s" >&2; return 1; }
}
run "budget cap: total sleep never exceeds BUDGET_SEC (non-divisible interval)" test_budget_cap_on_non_divisible_interval

# --- Test 12: numeric run-id validation ---------------------------------------
test_non_numeric_run_id_rejected() {
  reset_mocks
  queue_fixtures non-numeric-id.json
  local stderr rc=0
  stderr=$(main jbaruch coding-policy "$SHA_MERGE" "$WORKFLOW" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  echo "$stderr" | grep -q "expected numeric run id" || { echo "    FAIL: stderr should explain numeric validation, got: ${stderr}" >&2; return 1; }
}
run "non-numeric run id rejected with diagnostic" test_non_numeric_run_id_rejected

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
