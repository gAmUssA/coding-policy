#!/usr/bin/env bash
# Outcome-based tests for verify-publish-landed.sh, focused on the
# success/failure paths of the publish-landed conjunction (issue #80):
# this PR's resolved publish run must conclude `success` AND the
# registry's `Latest Version` must strictly advance past the pre-merge
# baseline. Either signal alone is insufficient — the conjunction
# closes the queued/in-flight-publish race where an interleaved
# earlier publish advances the registry while ours fails.
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` and `tessl` with shell functions in the
# test shell. Call `main` directly (NOT `$SCRIPT` as a subprocess —
# the mocks wouldn't propagate). Wrap `main` in a command substitution
# so `exit 1`/`exit 2` terminate only the subshell.
#
# Run: bash skills/release/tests/test_verify_publish_landed.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/verify-publish-landed.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: verify-publish-landed.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

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

# Mocks. MOCK_RUN_CONCLUSION feeds `gh run view --jq .conclusion`;
# MOCK_REGISTRY_VERSION is the latest published version the versions API
# (`tessl api v1/tiles/<ws>/<tile>/versions`) reports. Tests set these
# per-scenario.
gh() {
  case "$1" in
    run)
      local subcmd="$2"
      shift 2
      case "$subcmd" in
        view)
          # Contract: `gh run view <id> --json conclusion --jq '.conclusion'`.
          # Validate args explicitly so a regression that asks for the wrong
          # field surfaces as a loud mock failure.
          local saw_json=0 json_args="" jq_filter=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              --json) saw_json=1; json_args="${2:-}"; shift 2 ;;
              --jq)   jq_filter="${2:-}"; shift 2 ;;
              *)      shift ;;
            esac
          done
          [[ $saw_json -eq 1 ]] || { echo "mock gh run view: missing --json flag" >&2; return 99; }
          [[ "$json_args" == "conclusion" ]] || { echo "mock gh run view: wrong --json args: '${json_args}' (expected 'conclusion')" >&2; return 99; }
          [[ "$jq_filter" == ".conclusion" ]] || { echo "mock gh run view: wrong --jq filter: '${jq_filter}' (expected '.conclusion')" >&2; return 99; }
          # `${VAR-default}` (no colon) uses the default only when VAR
          # is unset, so a test setting MOCK_RUN_CONCLUSION="" still
          # exercises the empty-conclusion path (the colon form would
          # fall through to "success" on empty).
          printf '%s\n' "${MOCK_RUN_CONCLUSION-success}"
          ;;
        *) echo "mock gh run: unsupported subcommand: $subcmd" >&2; return 2 ;;
      esac
      ;;
    *) echo "mock gh: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# Emit a versions-API page whose maximum version is $1. Includes a lower
# entry (and lists them oldest-first) so the script's max-selection jq is
# genuinely exercised rather than reading a single-element list or relying
# on the array's order.
emit_versions_page() {
  local max="$1"
  printf '{"data":[{"attributes":{"version":"0.0.1"}},{"attributes":{"version":"%s"}}]}\n' "$max"
}

tessl() {
  case "$1" in
    api)
      # `tessl api v1/tiles/<ws>/<tile>/versions` -> one JSON page. The script
      # derives the latest published version as the numeric max across
      # .data[].attributes.version.
      [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
      emit_versions_page "${MOCK_REGISTRY_VERSION:-0.3.31}"
      ;;
    *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# --- test bodies ---

# Happy path: run succeeded AND registry advanced past PRE -> ok=true.
t_success_and_advance_returns_ok() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.32"
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "ok"               "true"                  "$(echo "$out" | jq -r .ok)"             || return 1
  assert_eq "run_conclusion"   "success"               "$(echo "$out" | jq -r .run_conclusion)" || return 1
  assert_eq "pre"              "0.3.31"                "$(echo "$out" | jq -r .pre)"            || return 1
  assert_eq "current"          "0.3.32"                "$(echo "$out" | jq -r .current)"        || return 1
  local reason
  reason=$(echo "$out" | jq -r .reason)
  [[ "$reason" == *"publish landed"* ]] || { echo "    FAIL: expected 'publish landed' in reason, got: $reason" >&2; return 1; }
}

# Issue #80 race: an interleaved earlier publish advanced the registry
# while ours failed. Pre-fix contract would have reported success
# because `current > PRE`; new contract reports failure because OUR run
# concluded `failure`.
t_race_failure_with_advance_returns_not_ok() {
  MOCK_RUN_CONCLUSION="failure"
  MOCK_REGISTRY_VERSION="0.3.32"  # interleaved publish advanced it
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code"        "1"          "$rc"                                    || return 1
  assert_eq "ok"               "false"      "$(echo "$out" | jq -r .ok)"             || return 1
  assert_eq "run_conclusion"   "failure"    "$(echo "$out" | jq -r .run_conclusion)" || return 1
  local reason
  reason=$(echo "$out" | jq -r .reason)
  [[ "$reason" == *"interleaved"* ]] || { echo "    FAIL: expected 'interleaved' in reason, got: $reason" >&2; return 1; }
}

# Skipped-publish: workflow exited success but didn't actually publish
# (e.g., conditional skip). Registry didn't advance -> ok=false.
t_success_but_no_advance_returns_not_ok() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.31"  # unchanged from PRE
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code"        "1"          "$rc"                                    || return 1
  assert_eq "ok"               "false"      "$(echo "$out" | jq -r .ok)"             || return 1
  local reason
  reason=$(echo "$out" | jq -r .reason)
  [[ "$reason" == *"skip"* || "$reason" == *"no-op"* ]] || { echo "    FAIL: expected 'skip' or 'no-op' in reason, got: $reason" >&2; return 1; }
}

# Plain failure with no registry change at all.
t_failure_with_no_advance_returns_not_ok() {
  MOCK_RUN_CONCLUSION="failure"
  MOCK_REGISTRY_VERSION="0.3.31"
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code"        "1"          "$rc"                                    || return 1
  assert_eq "ok"               "false"      "$(echo "$out" | jq -r .ok)"             || return 1
}

# Other terminal conclusions (cancelled, timed_out, etc.) must trigger
# the same disqualification as a plain failure. Pick one canonical
# example here; the script's branch is "conclusion != success" so all
# non-success values share the path.
t_cancelled_conclusion_returns_not_ok() {
  MOCK_RUN_CONCLUSION="cancelled"
  MOCK_REGISTRY_VERSION="0.3.32"
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code"        "1"             "$rc"                                    || return 1
  assert_eq "run_conclusion"   "cancelled"     "$(echo "$out" | jq -r .run_conclusion)" || return 1
}

# Semver comparison must be version-aware: 0.3.10 must rank greater
# than 0.3.9. A plain lexical compare would fail this case.
t_semver_advance_double_digit_patch_returns_ok() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.10"
  local out rc=0
  out=$(main gamussa coding-policy "0.3.9" "12345") || rc=$?
  assert_eq "exit code" "0"      "$rc"                          || return 1
  assert_eq "ok"        "true"   "$(echo "$out" | jq -r .ok)"
}

# Reject hypothetical downgrade. Shouldn't happen in practice but the
# contract is total: anything not strictly greater fails.
t_downgrade_returns_not_ok() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.30"
  local out rc=0
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  assert_eq "exit code" "1"       "$rc"                          || return 1
  assert_eq "ok"        "false"   "$(echo "$out" | jq -r .ok)"
}

# Argument validation. Non-numeric run-id must exit 2 (input error),
# not 1 (publish failed) — the script can't make a finding without a
# valid input.
t_invalid_run_id_exits_two() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.32"
  local rc=0
  ( main gamussa coding-policy "0.3.31" "not-a-number" >/dev/null 2>&1 ) || rc=$?
  assert_eq "exit code for non-numeric run-id" "2" "$rc"
}

t_empty_pre_baseline_exits_two() {
  local rc=0
  ( main gamussa coding-policy "" "12345" >/dev/null 2>&1 ) || rc=$?
  assert_eq "exit code for empty pre-baseline" "2" "$rc"
}

t_wrong_arg_count_exits_two() {
  local rc=0
  ( main gamussa coding-policy "0.3.31" >/dev/null 2>&1 ) || rc=$?
  assert_eq "exit code for wrong arg count" "2" "$rc"
}

# run-id == 0 must be rejected — the regex aligned with
# resolve-publish-run.sh's validate_positive_int (^[1-9][0-9]*$) now
# bars the bare zero that the prior ^[0-9]+$ accepted.
t_zero_run_id_exits_two() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.32"
  local rc=0
  ( main gamussa coding-policy "0.3.31" "0" >/dev/null 2>&1 ) || rc=$?
  assert_eq "exit code for run-id == 0" "2" "$rc"
}

# In-flight conclusion: `gh run view --jq '.conclusion'` returns the
# literal string "null" before the run reaches a terminal state. The
# script must distinguish this from a real terminal "failure"
# conclusion — otherwise it would mis-fire and report "publish failed"
# against a run that hasn't actually finished. Exits 2 (tool-state
# error, not a publish-landed/-failed finding).
t_null_conclusion_in_flight_exits_two() {
  MOCK_RUN_CONCLUSION="null"
  MOCK_REGISTRY_VERSION="0.3.32"
  local rc=0 stderr
  stderr=$( ( main gamussa coding-policy "0.3.31" "12345" >/dev/null ) 2>&1 ) || rc=$?
  assert_eq "exit code for in-flight (null) conclusion" "2" "$rc" || return 1
  [[ "$stderr" == *"in flight"* ]] || { echo "    FAIL: expected 'in flight' in stderr, got: $stderr" >&2; return 1; }
}

# Empty conclusion (some gh versions / contexts emit empty rather than
# "null") must also surface as "still in flight", not a finding.
t_empty_conclusion_exits_two() {
  MOCK_RUN_CONCLUSION=""
  MOCK_REGISTRY_VERSION="0.3.32"
  local rc=0
  ( main gamussa coding-policy "0.3.31" "12345" >/dev/null 2>&1 ) || rc=$?
  assert_eq "exit code for empty conclusion" "2" "$rc"
}

# Versions API returns no versions — an empty `.data` (or a shape carrying no
# .version) must surface as a tool-state error (exit 2) with the offending
# body included, not pass through to the conjunction with an empty `current`.
t_tessl_empty_versions_exits_two() {
  MOCK_RUN_CONCLUSION="success"
  # Override tessl mock for this test: a well-formed page with zero versions.
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           printf '{"data":[]}\n' ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
  local rc=0 stderr
  stderr=$( ( main gamussa coding-policy "0.3.31" "12345" >/dev/null ) 2>&1 ) || rc=$?
  assert_eq "exit code for empty versions API" "2" "$rc" || return 1
  [[ "$stderr" == *"no published version"* ]] || { echo "    FAIL: expected 'no published version' in diagnostic; got: $stderr" >&2; return 1; }
  # Restore the canonical mock so subsequent tests aren't affected.
  unset -f tessl
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           emit_versions_page "${MOCK_REGISTRY_VERSION:-0.3.31}" ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
}

# Versions API returns a non-JSON body (proxy error page, HTML 502) — must
# exit 2 with the "not valid JSON" diagnostic, never mis-parse it as a version.
t_tessl_non_json_exits_two() {
  MOCK_RUN_CONCLUSION="success"
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           printf '<html>502 Bad Gateway</html>\n' ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
  local rc=0 stderr
  stderr=$( ( main gamussa coding-policy "0.3.31" "12345" >/dev/null ) 2>&1 ) || rc=$?
  assert_eq "exit code for non-JSON versions body" "2" "$rc" || return 1
  [[ "$stderr" == *"not valid JSON"* ]] || { echo "    FAIL: expected 'not valid JSON' in diagnostic; got: $stderr" >&2; return 1; }
  unset -f tessl
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           emit_versions_page "${MOCK_REGISTRY_VERSION:-0.3.31}" ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
}

# Tests above source the script and then `set +e` so the test driver
# can assert exit codes without aborting on each subshell's exit. That
# weaker mode would mask any errexit-sensitive regression. This case
# explicitly re-enables `set -euo pipefail` inside a subshell before
# invoking `main` against an empty versions page, so any future `set -e`
# regression in the derivation surfaces here rather than being swallowed
# by the suite's `set +e`.
t_main_runs_under_errexit_pipefail() {
  MOCK_RUN_CONCLUSION="success"
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           printf '{"data":[]}\n' ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
  local rc=0 stderr
  stderr=$( ( set -e; set -o pipefail; set -u; main gamussa coding-policy "0.3.31" "12345" >/dev/null ) 2>&1 ) || rc=$?
  # Restore the canonical mock so subsequent tests aren't affected.
  unset -f tessl
  tessl() {
    case "$1" in
      api) [[ "$2" == *"/versions" ]] || { echo "mock tessl api: unsupported endpoint: $2" >&2; return 2; }
           emit_versions_page "${MOCK_REGISTRY_VERSION:-0.3.31}" ;;
      *) echo "mock tessl: unsupported invocation: $*" >&2; return 2 ;;
    esac
  }
  assert_eq "exit code under set -euo pipefail" "2" "$rc" || return 1
  [[ "$stderr" == *"no published version"* ]] || { echo "    FAIL: expected 'no published version' in stderr; got: $stderr" >&2; return 1; }
}

# Find a PATH that excludes jq. macOS ships `/usr/bin/jq` and Linux
# distros usually drop jq in `/usr/bin/jq` too, so a pure-/bin PATH is
# the most portable jq-free environment. Echo the path; caller checks
# the exit code (1 = skip, 0 = path captured).
no_jq_path() {
  local path="/bin"
  if PATH="$path" command -v jq >/dev/null 2>&1; then
    echo "    SKIP: cannot construct a jq-free PATH on this system" >&2
    return 1
  fi
  echo "$path"
}

# Missing-jq must (a) emit a parseable JSON envelope on stdout so
# callers parsing stdout still see the failure AND (b) emit an
# actionable diagnostic to stderr per rules/script-delegation.md's
# Self-error-handling requirement. Runs as a subprocess in a jq-free
# PATH — sourcing the script when jq is available would skip the guard.
t_missing_jq_emits_json_AND_stderr() {
  local path
  path=$(no_jq_path) || return 0  # SKIP returns 0 to avoid noisy fail
  local out err_file rc=0 err
  err_file=$(mktemp)
  out=$(env -i PATH="$path" HOME="$HOME" "$SCRIPT" gamussa coding-policy "0.3.31" "12345" 2>"$err_file") || rc=$?
  err=$(cat "$err_file"); rm -f "$err_file"
  assert_eq "exit code" "2" "$rc" || return 1
  # JSON envelope on stdout (caller-parses-stdout contract still holds).
  echo "$out" | env -i PATH="$PATH" jq -e . >/dev/null || { echo "    FAIL: stdout is not valid JSON: $out" >&2; return 1; }
  assert_eq "ok" "false" "$(echo "$out" | env -i PATH="$PATH" jq -r .ok)" || return 1
  [[ "$(echo "$out" | env -i PATH="$PATH" jq -r .reason)" == *"jq is not installed"* ]] || { echo "    FAIL: missing 'jq is not installed' in JSON reason" >&2; return 1; }
  # Stderr diagnostic per script-delegation.md.
  [[ -n "$err" ]] || { echo "    FAIL: stderr is empty (script-delegation.md requires a diagnostic on stderr)" >&2; return 1; }
  [[ "$err" == *"jq is not installed"* ]] || { echo "    FAIL: stderr missing 'jq is not installed': $err" >&2; return 1; }
}

# JSON shape — every reported case must be parseable JSON with the
# documented fields. Guards against future formatting drift.
t_output_is_valid_json_with_documented_shape() {
  MOCK_RUN_CONCLUSION="success"
  MOCK_REGISTRY_VERSION="0.3.32"
  local out rc=0 keys
  out=$(main gamussa coding-policy "0.3.31" "12345") || rc=$?
  echo "$out" | jq -e . >/dev/null || { echo "    FAIL: stdout not valid JSON: $out" >&2; return 1; }
  keys=$(echo "$out" | jq -r 'keys | sort | join(",")')
  assert_eq "keys" "current,ok,pre,reason,run_conclusion" "$keys"
}

# --- driver ---

echo "== verify-publish-landed.sh tests =="
run "success + advance -> ok=true"                                 t_success_and_advance_returns_ok
run "issue #80 race: failure + interleaved advance -> ok=false"    t_race_failure_with_advance_returns_not_ok
run "success + no advance -> ok=false (skipped publish)"           t_success_but_no_advance_returns_not_ok
run "failure + no advance -> ok=false"                             t_failure_with_no_advance_returns_not_ok
run "cancelled conclusion + advance -> ok=false"                   t_cancelled_conclusion_returns_not_ok
run "semver: 0.3.9 -> 0.3.10 (success) -> ok=true"                 t_semver_advance_double_digit_patch_returns_ok
run "downgrade (success) -> ok=false"                              t_downgrade_returns_not_ok
run "non-numeric run-id exits 2"                                   t_invalid_run_id_exits_two
run "empty pre-baseline exits 2"                                   t_empty_pre_baseline_exits_two
run "wrong arg count exits 2"                                      t_wrong_arg_count_exits_two
run "run-id == 0 exits 2 (matches positive-integer contract)"      t_zero_run_id_exits_two
run "in-flight (null) conclusion exits 2"                          t_null_conclusion_in_flight_exits_two
run "empty conclusion exits 2"                                     t_empty_conclusion_exits_two
run "empty versions API exits 2 with offending body"               t_tessl_empty_versions_exits_two
run "non-JSON versions body exits 2 (not valid JSON)"              t_tessl_non_json_exits_two
run "main runs safely under set -euo pipefail"                     t_main_runs_under_errexit_pipefail
run "missing jq emits JSON on stdout AND diagnostic on stderr"     t_missing_jq_emits_json_AND_stderr
run "output is valid JSON with documented shape"                   t_output_is_valid_json_with_documented_shape

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
