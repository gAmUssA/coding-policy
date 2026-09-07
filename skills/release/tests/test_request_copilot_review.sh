#!/usr/bin/env bash
# Outcome-based tests for request-copilot-review.sh covering:
#   - fetch_pr_node_id refuses null PR IDs upfront (#42);
#   - discover_copilot_bot_id matches Copilot's login with or without the
#     [bot] suffix — GraphQL Bot.login is inconsistent across contexts (#43);
#   - main() verifies the request from the mutation's OWN returned review
#     requests, not the REST `requested_reviewers` field that omits bot
#     reviewers (#276) — a bot-only reviewer list must verify clean, the case
#     the earlier suite never exercised, which let the REST-verify bug ship;
#   - the mutation runs in union mode (#297) — replace mode reports success and
#     lands nothing for a bot-only request, so only the query TEXT can catch it;
#   - a terminal retry failure surfaces the GraphQL error (#297).
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that returns
# fixture JSON keyed off MOCK_GH_FIXTURE, applying the --jq filter the
# script passes so the filter logic itself is exercised, not just
# duplicated in the test.
#
# Run: bash skills/release/tests/test_request_copilot_review.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/request-copilot-review.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: request-copilot-review.sh not executable at $SCRIPT" >&2; exit 2; }

# Source the script so we can call its helper functions directly. The
# script's `[[ BASH_SOURCE[0] == $0 ]] && main "$@"` guard prevents
# main() from running when sourced (the comparison is false), but the
# guard line itself returns exit code 1 from the failed `[[ ]]`, and
# the script's `set -e` then propagates that 1 back through the source
# operation. Wrap with `|| true` so the outer test driver doesn't get
# nuked by what is effectively the script's idiomatic no-op-when-sourced
# path. After sourcing, also flip errexit back off so per-test
# assertions returning non-zero don't abort the driver.
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

# Mock `gh` — responds to `gh api graphql ... --jq <filter>` by piping
# the fixture (selected by MOCK_GH_FIXTURE) through the requested jq
# filter. This is intentional: the filter is the contract that changed
# in #42 and #43, so the mock applies it the same way the real `gh`
# would. Anything that isn't `gh api graphql` is unsupported here.
gh() {
  if [[ "$1" != "api" || "$2" != "graphql" ]]; then
    echo "mock gh: unsupported invocation: $*" >&2
    return 2
  fi
  local filter=""
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --jq) filter="$2"; shift 2 ;;
      *)    shift ;;
    esac
  done
  local fixture
  case "${MOCK_GH_FIXTURE:-}" in
    pr_not_found)
      fixture='{"data":{"repository":{"pullRequest":null}}}' ;;
    pr_found)
      fixture='{"data":{"repository":{"pullRequest":{"id":"PR_kwDOFAKE"}}}}' ;;
    bot_with_suffix)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_withSuffix","login":"copilot-pull-request-reviewer[bot]"}}]}}]}}}}' ;;
    bot_bare_login)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_bareLogin","login":"copilot-pull-request-reviewer"}}]}}]}}}}' ;;
    bot_no_match)
      fixture='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_other","login":"some-other-bot[bot]"}}]}}]}}}}' ;;
    *)
      echo "mock gh: unknown MOCK_GH_FIXTURE='${MOCK_GH_FIXTURE:-}'" >&2
      return 2 ;;
  esac
  if [[ -n "$filter" ]]; then
    echo "$fixture" | jq -r "$filter"
  else
    echo "$fixture"
  fi
}

# A content-aware graphql mock for driving main(): it distinguishes the
# PR-id query, the requestReviews mutation, and the discover query by their
# text, and applies the script's own --jq filter so the real filter logic is
# exercised. Fixtures and behaviour come from env vars the caller sets:
#   MUT_FIXTURE       requestReviews response JSON
#   DISCOVER_FIXTURE  review-history response JSON (bot-id discovery)
#   MUT_FAIL_ONCE + MUT_FAIL_FLAG  fail the FIRST mutation once (stale-ID path)
# shellcheck disable=SC2329  # invoked indirectly via a per-test gh() override
_main_gh_mock() {
  [[ "$1" == api && "$2" == graphql ]] || { echo "mock gh: unsupported: $*" >&2; return 2; }
  local query="" filter=""
  shift 2
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f)   query="${2#query=}"; shift 2 ;;
      --jq) filter="$2"; shift 2 ;;
      *)    shift ;;
    esac
  done
  local fixture
  if [[ "$query" == *requestReviews* ]]; then
    # MUT_FAIL_ALWAYS fails BOTH attempts, so the retry path is terminal — the
    # only path whose diagnostic has to carry the GraphQL error text.
    if [[ -n "${MUT_FAIL_ALWAYS:-}" ]]; then
      echo "mock gh: GraphQL: Could not resolve to a node with the global id of 'BOT_stale'" >&2
      return 1
    fi
    if [[ -n "${MUT_FAIL_ONCE:-}" && ! -f "${MUT_FAIL_FLAG:-/nonexistent}" ]]; then
      : > "${MUT_FAIL_FLAG}"
      echo "mock gh: requestReviews rejected the botId" >&2
      return 1
    fi
    fixture="${MUT_FIXTURE:?MUT_FIXTURE unset}"
  elif [[ "$query" == *"pullRequest(number"* ]]; then
    fixture='{"data":{"repository":{"pullRequest":{"id":"PR_kwDOX"}}}}'
  elif [[ "$query" == *"pullRequests(last"* ]]; then
    fixture="${DISCOVER_FIXTURE:?DISCOVER_FIXTURE unset}"
  else
    echo "mock gh: unrecognized query: $query" >&2; return 2
  fi
  if [[ -n "$filter" ]]; then
    echo "$fixture" | jq -r "$filter"
  else
    echo "$fixture"
  fi
}

# --- test bodies ---

t_fetch_pr_node_id_returns_empty_and_nonzero_on_null_pr() {
  MOCK_GH_FIXTURE=pr_not_found
  local out rc
  out=$(fetch_pr_node_id "owner" "repo" "999" 2>/dev/null)
  rc=$?
  assert_eq "exit code"  "1"  "$rc"  || return 1
  assert_eq "stdout"     ""   "$out" || return 1
}

t_fetch_pr_node_id_refuses_non_numeric_pr_number() {
  # `gh` should never be called when the input is rejected upfront.
  # Override the mock inside a subshell so a future regression where
  # the validation gate is skipped surfaces as a loud test failure
  # without leaking the override into the rest of the test file.
  local err rc
  err=$(
    gh() { echo "mock gh: should not be called for non-numeric input" >&2; return 99; }
    fetch_pr_node_id "owner" "repo" "abc" 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"must be a positive integer"* ]] || { echo "    FAIL: stderr missing 'must be a positive integer': $err" >&2; return 1; }
}

t_fetch_pr_node_id_returns_id_on_real_pr() {
  MOCK_GH_FIXTURE=pr_found
  local out rc
  out=$(fetch_pr_node_id "owner" "repo" "1")
  rc=$?
  assert_eq "exit code"  "0"            "$rc"  || return 1
  assert_eq "stdout"     "PR_kwDOFAKE"  "$out"
}

t_discover_matches_bot_suffix_login() {
  MOCK_GH_FIXTURE=bot_with_suffix
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "id" "BOT_withSuffix" "$out"
}

t_discover_matches_bare_login() {
  MOCK_GH_FIXTURE=bot_bare_login
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "id" "BOT_bareLogin" "$out"
}

t_discover_returns_empty_when_no_copilot_review() {
  MOCK_GH_FIXTURE=bot_no_match
  local out
  out=$(discover_copilot_bot_id "owner" "repo")
  assert_eq "stdout" "" "$out"
}

# #276: a request that attaches only a bot reviewer must verify CLEAN. Under
# the old REST verification this list came back `[]` and main() always exited
# 1; verifying from the mutation response fixes it.
t_main_verifies_bot_only_reviewers() {
  local out rc
  out=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}' \
      main owner repo 5 2>/dev/null
  )
  rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  echo "$out" | jq -e '.requested_reviewers | any(test("copilot"; "i"))' >/dev/null 2>&1 \
    || { echo "    FAIL: output envelope missing copilot: $out" >&2; return 1; }
}

# A mutation whose returned reviewers do NOT include Copilot is a real failure.
t_main_fails_when_copilot_absent() {
  local err rc
  err=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"some-other-bot"}}]}}}}}' \
      main owner repo 5 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"not in review requests"* ]] \
    || { echo "    FAIL: stderr missing 'not in review requests': $err" >&2; return 1; }
}

# A rejected pinned bot ID falls back to discovery, then verifies from the
# retried mutation's response.
t_main_falls_back_on_rejected_pinned_id() {
  local out rc flag
  flag=$(mktemp -u)
  out=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FAIL_ONCE=1 MUT_FAIL_FLAG="$flag" \
    DISCOVER_FIXTURE='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_discovered","login":"copilot-pull-request-reviewer"}}]}}]}}}}' \
    MUT_FIXTURE='{"data":{"requestReviews":{"pullRequest":{"reviewRequests":{"nodes":[{"requestedReviewer":{"__typename":"Bot","login":"copilot-pull-request-reviewer"}}]}}}}}' \
      main owner repo 5 2>/dev/null
  )
  rc=$?
  rm -f "$flag"
  assert_eq "exit code" "0" "$rc" || return 1
  echo "$out" | jq -e '.bot_id == "BOT_discovered"' >/dev/null 2>&1 \
    || { echo "    FAIL: expected the discovered bot id in the output: $out" >&2; return 1; }
}

# #297: without `union: true` the mutation runs in replace mode, which returns
# success and lands nothing for a bot-only request — `reviewRequests` comes
# back empty and the script exits 1 on every invocation against a valid bot ID.
# A mocked response cannot reproduce that, so assert the query TEXT: the mode
# is the contract here, and it is invisible everywhere else.
t_mutation_runs_in_union_mode() {
  local query_file query
  query_file=$(mktemp) || { echo "    FAIL: mktemp failed" >&2; return 1; }
  (
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced request_with_bot_id
    gh() {
      local q=""
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -f) q="${2#query=}"; shift 2 ;;
          *)  shift ;;
        esac
      done
      printf '%s' "$q" > "$query_file"
      echo '[]'
    }
    request_with_bot_id "PR_kwDOX" "BOT_kgDOCnlnWA" >/dev/null
  )
  query=$(cat "$query_file")
  rm -f "$query_file"
  case "$query" in
    *"union: true"*) return 0 ;;
    *) echo "    FAIL: requestReviews mutation is missing 'union: true': ${query}" >&2; return 1 ;;
  esac
}

# #297: both attempts discarded the mutation's stderr, so a terminal failure
# reported only "request failed with discovered bot ID X" — indistinguishable
# between a rejected bot ID and an auth or network fault. The retry path now
# captures and surfaces the GraphQL error.
t_main_surfaces_mutation_error_on_terminal_failure() {
  local err rc
  err=$(
    # shellcheck disable=SC2317  # gh() runs indirectly through the sourced main(); shellcheck cannot trace the call
    gh() { _main_gh_mock "$@"; }
    MUT_FAIL_ALWAYS=1 \
    DISCOVER_FIXTURE='{"data":{"repository":{"pullRequests":{"nodes":[{"reviews":{"nodes":[{"author":{"id":"BOT_discovered","login":"copilot-pull-request-reviewer"}}]}}]}}}}' \
      main owner repo 5 2>&1 >/dev/null
  )
  rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$err" == *"Could not resolve to a node"* ]] \
    || { echo "    FAIL: stderr missing the GraphQL error text: $err" >&2; return 1; }
}

# --- driver ---

echo "== request-copilot-review.sh tests =="
run "fetch_pr_node_id refuses null PR with non-zero exit"            t_fetch_pr_node_id_returns_empty_and_nonzero_on_null_pr
run "fetch_pr_node_id refuses non-numeric pr-number argument"        t_fetch_pr_node_id_refuses_non_numeric_pr_number
run "fetch_pr_node_id returns ID for a real PR"                      t_fetch_pr_node_id_returns_id_on_real_pr
run "discover_copilot_bot_id matches Bot.login with [bot] suffix"    t_discover_matches_bot_suffix_login
run "discover_copilot_bot_id matches Bot.login without suffix"       t_discover_matches_bare_login
run "discover_copilot_bot_id returns empty when no Copilot review"   t_discover_returns_empty_when_no_copilot_review
run "main verifies a bot-only reviewer list clean (#276)"           t_main_verifies_bot_only_reviewers
run "main fails when Copilot is absent from the mutation response"   t_main_fails_when_copilot_absent
run "main falls back to discovery on a rejected pinned bot ID"       t_main_falls_back_on_rejected_pinned_id
run "requestReviews mutation runs in union mode (#297)"              t_mutation_runs_in_union_mode
run "main surfaces the GraphQL error on a terminal failure (#297)"   t_main_surfaces_mutation_error_on_terminal_failure

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
