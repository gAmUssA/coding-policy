#!/usr/bin/env bash
# Outcome-based tests for dismiss-stale-reviews.sh.
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that (a) serves a
# fixture reviews list for the GET reviews surface and (b) records every
# dismissal PUT into $DISMISS_LOG. jq runs locally so the script's real
# decision logic is exercised, not duplicated in the test.
#
# Run: bash skills/release/tests/test_dismiss_stale_reviews.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/dismiss-stale-reviews.sh"
[[ -r "$SCRIPT" ]] || { echo "fatal: dismiss-stale-reviews.sh not readable at $SCRIPT" >&2; exit 2; }

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
  DISMISS_LOG="$(mktemp)"
  export DISMISS_LOG
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
  rm -f "$DISMISS_LOG"
}

# Mock `gh api` — two surfaces:
#   GET  repos/<o>/<r>/pulls/<N>/reviews?per_page=100   -> $MOCK_REVIEWS_BODY
#   PUT  repos/<o>/<r>/pulls/<N>/reviews/<id>/dismissals -> log <id>, return {}
gh() {
  [[ "$1" == "api" ]] || { echo "mock gh: unsupported invocation: $*" >&2; return 2; }
  shift
  local method="GET" path="" saw_paginate=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -X)         method="$2"; shift 2 ;;
      --paginate) saw_paginate=1; shift ;;
      -f)         shift 2 ;;   # message=... / event=DISMISS
      repos/*)    path="$1"; shift ;;
      *)          shift ;;
    esac
  done

  if [[ "$method" == "PUT" && "$path" == *"/dismissals" ]]; then
    local rid="${path%/dismissals}"; rid="${rid##*/}"
    echo "$rid" >> "$DISMISS_LOG"
    echo '{}'
    return 0
  fi

  [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: reviews fetch missing --paginate" >&2; return 99; }
  case "$path" in
    *reviews*) echo "${MOCK_REVIEWS_BODY:-[]}" ;;
    *) echo "mock gh api: unsupported path: $path" >&2; return 2 ;;
  esac
}

# Convenience: how many dismissals were issued this test.
dismiss_count() { [[ -s "$DISMISS_LOG" ]] || { echo 0; return; }; wc -l < "$DISMISS_LOG" | tr -d ' '; }

# --- test bodies ---

# Stale CHANGES_REQUESTED superseded by a later COMMENT from the same bot
# is dismissed. This is the everyday case: bot requests changes, agent
# fixes, bot re-reviews clean with a COMMENT (it cannot APPROVE — 422).
t_stale_cr_then_comment_is_dismissed() {
  MOCK_REVIEWS_BODY='[
    {"id":11,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":12,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out ids n
  out=$(main "owner" "repo" "1") || { echo "    main exited non-zero" >&2; return 1; }
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  n=$(dismiss_count)
  assert_eq "dismissed review_id" "11" "$ids" || return 1
  assert_eq "PUT dismissal count" "1" "$n"     || return 1
  assert_eq "logged dismissed id" "11" "$(tr -d '\n' < "$DISMISS_LOG")"
}

# Latest review is still CHANGES_REQUESTED -> leave it gating, dismiss nothing.
t_latest_cr_is_left_active() {
  MOCK_REVIEWS_BODY='[
    {"id":21,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":22,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":23,"state":"CHANGES_REQUESTED","commit_id":"ccc","submitted_at":"2026-01-03T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out active n
  out=$(main "owner" "repo" "1") || return 1
  active=$(jq -r '.left_active | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  n=$(dismiss_count)
  assert_eq "left_active entry" "github-actions[bot]:23" "$active" || return 1
  assert_eq "no dismissals"     "0"                      "$n"      || return 1
  assert_eq "dismissed empty"   "0" "$(jq '.dismissed | length' <<<"$out")"
}

# An already-DISMISSED stale review is not re-dismissed (idempotent re-run).
t_already_dismissed_is_skipped() {
  MOCK_REVIEWS_BODY='[
    {"id":31,"state":"DISMISSED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":32,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out n
  out=$(main "owner" "repo" "1") || return 1
  n=$(dismiss_count)
  assert_eq "no dismissals" "0" "$n" || return 1
  assert_eq "dismissed empty" "0" "$(jq '.dismissed | length' <<<"$out")"
}

# Per-bot independence: one bot cleared (dismiss its stale CR), the other
# still requesting changes (leave active).
t_per_bot_independence() {
  MOCK_REVIEWS_BODY='[
    {"id":41,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":42,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":43,"state":"CHANGES_REQUESTED","commit_id":"ccc","submitted_at":"2026-01-02T06:00:00Z","user":{"login":"copilot-pull-request-reviewer[bot]"}}
  ]'
  local out dismissed active
  out=$(main "owner" "repo" "1") || return 1
  dismissed=$(jq -r '.dismissed | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  active=$(jq -r '.left_active | map("\(.login):\(.review_id)") | join(",")' <<<"$out")
  assert_eq "dismissed codex stale"       "github-actions[bot]:41"                 "$dismissed" || return 1
  assert_eq "copilot left active"         "copilot-pull-request-reviewer[bot]:43"  "$active"    || return 1
  assert_eq "exactly one dismissal"       "1"                                      "$(dismiss_count)"
}

# No reviews at all -> empty envelope, exit 0.
t_no_reviews_is_noop() {
  MOCK_REVIEWS_BODY='[]'
  local out
  out=$(main "owner" "repo" "1") || return 1
  assert_eq "dismissed empty"   "0" "$(jq '.dismissed | length' <<<"$out")"   || return 1
  assert_eq "left_active empty" "0" "$(jq '.left_active | length' <<<"$out")" || return 1
  assert_eq "no dismissals"     "0" "$(dismiss_count)"
}

# Latest review is DISMISSED (not an all-clear) with an EARLIER active
# CHANGES_REQUESTED the bot never cleared -> dismiss nothing. A dismissed
# latest verdict is not a COMMENTED/APPROVED all-clear, so the earlier
# active request must stay put.
t_latest_dismissed_leaves_earlier_active_cr() {
  MOCK_REVIEWS_BODY='[
    {"id":61,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":62,"state":"DISMISSED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out n
  out=$(main "owner" "repo" "1") || return 1
  n=$(dismiss_count)
  assert_eq "no dismissals"   "0" "$n"                                   || return 1
  assert_eq "dismissed empty" "0" "$(jq '.dismissed | length' <<<"$out")"
}

# Two stale CRs before a clean COMMENT -> both dismissed.
t_multiple_stale_crs_all_dismissed() {
  MOCK_REVIEWS_BODY='[
    {"id":51,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":52,"state":"CHANGES_REQUESTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":53,"state":"COMMENTED","commit_id":"ccc","submitted_at":"2026-01-03T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local out ids
  out=$(main "owner" "repo" "1") || return 1
  ids=$(jq -r '.dismissed | map(.review_id) | sort | join(",")' <<<"$out")
  assert_eq "both stale dismissed" "51,52" "$ids" || return 1
  assert_eq "two dismissals"       "2"     "$(dismiss_count)"
}

# The bot logins contain `[bot]`, a glob bracket. If the loop iterated them
# unquoted, a file in the working directory matching the pattern (e.g.
# `github-actionsb`) would rewrite the login token via pathname expansion
# and the review would never be found. Run from a directory seeded with
# such a decoy file and assert the dismissal still happens.
t_login_is_glob_safe_against_cwd_files() {
  MOCK_REVIEWS_BODY='[
    {"id":71,"state":"CHANGES_REQUESTED","commit_id":"aaa","submitted_at":"2026-01-01T00:00:00Z","user":{"login":"github-actions[bot]"}},
    {"id":72,"state":"COMMENTED","commit_id":"bbb","submitted_at":"2026-01-02T00:00:00Z","user":{"login":"github-actions[bot]"}}
  ]'
  local decoy_dir out ids
  decoy_dir=$(mktemp -d)
  # Files that `github-actions[bot]` and `copilot-...[bot]` would glob to.
  : > "${decoy_dir}/github-actionsb"
  : > "${decoy_dir}/copilot-pull-request-reviewero"
  out=$(cd "$decoy_dir" && main "owner" "repo" "1") || { rm -rf "$decoy_dir"; return 1; }
  rm -rf "$decoy_dir"
  ids=$(jq -r '.dismissed | map(.review_id) | join(",")' <<<"$out")
  assert_eq "dismissed despite cwd decoy files" "71" "$ids" || return 1
  assert_eq "one dismissal" "1" "$(dismiss_count)"
}

# --- runner ---

echo "test_dismiss_stale_reviews.sh"
run "stale CR then COMMENT is dismissed"        t_stale_cr_then_comment_is_dismissed
run "latest CR is left active"                  t_latest_cr_is_left_active
run "already-dismissed is skipped"              t_already_dismissed_is_skipped
run "per-bot independence"                      t_per_bot_independence
run "no reviews is a no-op"                     t_no_reviews_is_noop
run "latest DISMISSED leaves earlier active CR" t_latest_dismissed_leaves_earlier_active_cr
run "multiple stale CRs all dismissed"          t_multiple_stale_crs_all_dismissed
run "login is glob-safe against cwd files"      t_login_is_glob_safe_against_cwd_files

echo
echo "passed: ${PASS_COUNT}, failed: ${FAIL_COUNT}"
[[ $FAIL_COUNT -eq 0 ]]
