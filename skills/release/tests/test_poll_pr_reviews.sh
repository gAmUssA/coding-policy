#!/usr/bin/env bash
# Outcome-based tests for poll-pr-reviews.sh, focused on the new
# `merge_state` output path: `fetch_merge_state` must return the
# `{status, mergeable}` envelope keyed off GitHub's `mergeStateStatus`
# and `mergeable` fields, and `main` must surface that envelope as a
# top-level field in the snapshot JSON.
#
# Approach: source the script (its main() guard prevents auto-run when
# sourced) and override `gh` with a shell function that returns
# fixture JSON for the two surfaces poll-pr-reviews.sh calls —
# `gh pr checks` and `gh pr view --json mergeStateStatus,mergeable` —
# plus the two `gh api` surfaces for reviews/comments. jq runs locally
# so the script's filter logic is exercised, not duplicated in the test.
#
# Run: bash skills/release/tests/test_poll_pr_reviews.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/poll-pr-reviews.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: poll-pr-reviews.sh not executable at $SCRIPT" >&2; exit 2; }

# shellcheck disable=SC1090
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

# The head SHA the mock's `gh pr view` returns. A review fixture whose
# commit_id equals this is "fresh" (bound to head); anything else is "stale"
# and its verdict resolves to state "none" (#186).
HEAD_SHA="head000000000000000000000000000000000000"
OLD_SHA="0ld00000000000000000000000000000000000000"

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

# Mock `gh` — supports the four invocations poll-pr-reviews.sh issues.
# MOCK_MERGE_STATE selects the fixture for `gh pr view`; other surfaces
# return minimal fixtures so main() can complete.
gh() {
  case "$1" in
    pr)
      local subcmd="$2"
      shift 2
      case "$subcmd" in
        view)
          # Contract: `gh pr view <N> --repo <o/r> --json mergeStateStatus,mergeable`.
          # Validate the --json args explicitly so a regression that drops --json
          # or asks for the wrong fields surfaces as a loud mock failure rather
          # than passing silently against a permissive stub.
          local saw_json=0 json_args=""
          while [[ $# -gt 0 ]]; do
            case "$1" in
              --json) saw_json=1; json_args="${2:-}"; shift 2 ;;
              *)      shift ;;
            esac
          done
          [[ $saw_json -eq 1 ]] || { echo "mock gh pr view: missing --json flag (contract: --json mergeStateStatus,mergeable,headRefOid)" >&2; return 99; }
          [[ "$json_args" == "mergeStateStatus,mergeable,headRefOid" ]] || { echo "mock gh pr view: wrong --json args: '${json_args}' (expected 'mergeStateStatus,mergeable,headRefOid')" >&2; return 99; }
          # headRefOid is the head SHA review verdicts are bound to. Tests that
          # exercise verdict resolution set review fixtures' commit_id to
          # HEAD_SHA (fresh) or something else (stale) — see HEAD_SHA below.
          case "${MOCK_MERGE_STATE:-}" in
            clean)        echo '{"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE","headRefOid":"'"$HEAD_SHA"'"}' ;;
            dirty)        echo '{"mergeStateStatus":"DIRTY","mergeable":"CONFLICTING","headRefOid":"'"$HEAD_SHA"'"}' ;;
            unknown)      echo '{"mergeStateStatus":"UNKNOWN","mergeable":"UNKNOWN","headRefOid":"'"$HEAD_SHA"'"}' ;;
            no-head)      echo '{"mergeStateStatus":"CLEAN","mergeable":"MERGEABLE","headRefOid":""}' ;;
            *) echo "mock gh: unknown MOCK_MERGE_STATE='${MOCK_MERGE_STATE:-}'" >&2; return 2 ;;
          esac
          ;;
        checks)
          # gh pr checks <N> --repo <o/r> --json name,bucket
          echo "${MOCK_CHECKS_BODY:-[]}"
          ;;
        *) echo "mock gh pr: unsupported subcommand: $subcmd" >&2; return 2 ;;
      esac
      ;;
    api)
      # gh api --paginate repos/<o>/<r>/pulls/<N>/reviews?per_page=100
      # gh api --paginate repos/<o>/<r>/pulls/<N>/comments?per_page=100
      # The script pipes the raw paginated output through `jq -s` itself,
      # so this mock no longer forwards `--jq`. It echoes a fixture body
      # keyed off the path; tests can simulate multiple pages by setting
      # MOCK_REVIEWS_BODY / MOCK_COMMENTS_BODY to several concatenated
      # JSON arrays (what `gh api --paginate` actually emits across pages).
      shift  # consume "api"
      local path="" saw_paginate=0
      while [[ $# -gt 0 ]]; do
        case "$1" in
          --paginate) saw_paginate=1; shift ;;
          --jq)       echo "mock gh api: --jq is incompatible with --paginate here; the script should jq -s externally" >&2; return 99 ;;
          *)          [[ -z "$path" ]] && path="$1"; shift ;;
        esac
      done
      [[ $saw_paginate -eq 1 ]] || { echo "mock gh api: missing --paginate (required so the script never silently misses page 2+)" >&2; return 99; }
      case "$path" in
        *reviews*)  echo "${MOCK_REVIEWS_BODY:-[]}" ;;
        *comments*) echo "${MOCK_COMMENTS_BODY:-[]}" ;;
        *) echo "mock gh api: unsupported path: $path" >&2; return 2 ;;
      esac
      ;;
    *) echo "mock gh: unsupported invocation: $*" >&2; return 2 ;;
  esac
}

# --- test bodies ---

t_fetch_merge_state_clean_returns_mergeable_envelope() {
  MOCK_MERGE_STATE=clean
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "CLEAN"     "$status"    || return 1
  assert_eq "mergeable" "MERGEABLE" "$mergeable"
}

t_fetch_merge_state_dirty_returns_conflicting_envelope() {
  MOCK_MERGE_STATE=dirty
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "DIRTY"       "$status"    || return 1
  assert_eq "mergeable" "CONFLICTING" "$mergeable"
}

t_fetch_merge_state_unknown_returns_unknown_envelope() {
  MOCK_MERGE_STATE=unknown
  local out status mergeable
  out=$(fetch_merge_state "owner" "repo" "1")
  status=$(echo "$out" | jq -r '.status')
  mergeable=$(echo "$out" | jq -r '.mergeable')
  assert_eq "status"    "UNKNOWN" "$status"    || return 1
  assert_eq "mergeable" "UNKNOWN" "$mergeable"
}

t_main_surfaces_merge_state_as_top_level_field() {
  MOCK_MERGE_STATE=clean
  local out keys
  out=$(main "owner" "repo" "1")
  keys=$(echo "$out" | jq -r '.merge_state | "\(.status)|\(.mergeable)"')
  assert_eq "merge_state in main output" "CLEAN|MERGEABLE" "$keys"
}

t_main_propagates_dirty_state() {
  MOCK_MERGE_STATE=dirty
  local out keys
  out=$(main "owner" "repo" "1")
  keys=$(echo "$out" | jq -r '.merge_state | "\(.status)|\(.mergeable)"')
  assert_eq "merge_state in main output" "DIRTY|CONFLICTING" "$keys"
}

# Issue #83: on PRs with > 1 page of reviews, gh api without --paginate
# returns only page 1. The pre-fix `| last` filter then picked the last
# entry on page 1 — not the actual newest review on the last page — and
# the gate could approve a merge against stale data.
#
# Build a fixture that mimics what `gh api --paginate` actually emits:
# two concatenated JSON arrays. Page 1's last entry is a COMMENTED review
# at 17:00; page 2's last entry is a CHANGES_REQUESTED review at 18:04.
# A correct implementation must report CHANGES_REQUESTED@18:04.
t_latest_review_by_picks_from_last_page() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-05-18T16:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-05-18T17:00:00Z"}][{"user":{"login":"github-actions[bot]"},"state":"CHANGES_REQUESTED","submitted_at":"2026-05-18T18:04:00Z"}]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "state from last page"        "CHANGES_REQUESTED"     "$state"        || return 1
  assert_eq "submitted_at from last page" "2026-05-18T18:04:00Z"  "$submitted_at"
}

t_latest_review_by_returns_none_when_no_reviews() {
  MOCK_REVIEWS_BODY='[]'
  local out state submitted_at body
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  body=$(echo "$out" | jq -r '.body')
  assert_eq "state for empty"        "none" "$state"        || return 1
  assert_eq "submitted_at for empty" "null" "$submitted_at" || return 1
  assert_eq "body for empty"         "null" "$body"
}

# A review's state classifies merge-gating, not whether its body must be read.
# A COMMENTED review with zero inline comments still carries a body the gate
# must surface so the agent reads it (rules/reviewer-feedback-reading.md).
t_latest_review_by_surfaces_body_text() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-05-18T16:00:00Z","body":"Non-blocking, but the rename in foo.py:42 drops the retry guard."}]'
  local out state body
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  body=$(echo "$out" | jq -r '.body')
  assert_eq "state surfaced"  "COMMENTED" "$state" || return 1
  assert_eq "body surfaced verbatim" "Non-blocking, but the rename in foo.py:42 drops the retry guard." "$body"
}

t_latest_review_by_filters_other_logins_across_pages() {
  # Page 1: two human reviews + one bot review. Page 2: one human review
  # that's newer than the bot review. The bot's latest is still the page-1
  # bot review, even though the page-2 human is newer.
  MOCK_REVIEWS_BODY='[{"user":{"login":"alice"},"state":"COMMENTED","submitted_at":"2026-05-18T15:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-05-18T16:00:00Z"},{"user":{"login":"bob"},"state":"COMMENTED","submitted_at":"2026-05-18T16:30:00Z"}][{"user":{"login":"alice"},"state":"COMMENTED","submitted_at":"2026-05-18T17:00:00Z"}]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "bot state"        "APPROVED"             "$state"        || return 1
  assert_eq "bot submitted_at" "2026-05-18T16:00:00Z" "$submitted_at"
}

# Same shape for comments: counts must sum across pages, not pick page 1
# alone. Mix in a non-target login and an in_reply_to_id to confirm the
# filter still discards both.
t_toplevel_comments_by_sums_across_pages() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"alice"},"in_reply_to_id":null}][{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":12345}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "github-actions[bot]")
  assert_eq "top-level bot comments across both pages" "3" "$count"
}

t_toplevel_comments_by_returns_zero_for_no_comments() {
  MOCK_COMMENTS_BODY='[]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "github-actions[bot]")
  assert_eq "comments count for empty" "0" "$count"
}

# Copilot authors its REVIEW as `copilot-pull-request-reviewer[bot]` but its
# INLINE COMMENTS as `Copilot`. Counting comments against the review login
# matched nothing, so `inline_comments.copilot` read 0 on every PR — vacuously
# satisfying the release skill's Step 7 "every inline comment has a reply" gate
# and letting a real Copilot finding merge unanswered.
t_toplevel_comments_by_counts_copilot_login() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"Copilot"},"in_reply_to_id":null,"path":"a/b.md","body":"Real finding."}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "${COPILOT_COMMENT_LOGINS[@]}")
  assert_eq "Copilot-authored comment is counted" "1" "$count"
}

# The review login must stay in the comment set: a regression that swapped one
# login for the other instead of matching both would pass the test above and
# still lose comments on any PR where Copilot posts under the review login.
t_toplevel_comments_by_matches_either_copilot_login() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"Copilot"},"in_reply_to_id":null},{"user":{"login":"copilot-pull-request-reviewer[bot]"},"in_reply_to_id":null}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "${COPILOT_COMMENT_LOGINS[@]}")
  assert_eq "both Copilot logins counted" "2" "$count"
}

t_toplevel_comments_by_excludes_replies_and_other_logins() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"Copilot"},"in_reply_to_id":null},{"user":{"login":"Copilot"},"in_reply_to_id":991},{"user":{"login":"some-human"},"in_reply_to_id":null}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "${COPILOT_COMMENT_LOGINS[@]}")
  assert_eq "replies and foreign logins excluded" "1" "$count"
}

# A degraded API response can put a non-object element (an error body, a bare
# string) in the array. Indexing it aborted jq mid-filter and failed the whole
# poll: watch-pr-reviews.sh then burned all 50 attempts and reported
# `pending_at_budget`, which reads as "no reviewer posted" rather than "the
# tool broke" (issue #300). Real comments around it must still be counted.
t_toplevel_comments_by_survives_non_object_element() {
  MOCK_COMMENTS_BODY='["Not Found",{"user":{"login":"Copilot"},"in_reply_to_id":null},{"user":{"login":"Copilot"},"in_reply_to_id":null}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "${COPILOT_COMMENT_LOGINS[@]}" 2>/dev/null)
  assert_eq "count survives a non-object element" "2" "$count"
}

# The drop is visible, never silent: best-effort work that continues past a
# failure warns (rules/error-handling.md Shell Error Handling).
t_toplevel_comments_by_warns_on_non_object_element() {
  MOCK_COMMENTS_BODY='["Not Found",{"user":{"login":"Copilot"},"in_reply_to_id":null}]'
  local err
  err=$( { toplevel_comments_by "owner" "repo" "1" "${COPILOT_COMMENT_LOGINS[@]}" >/dev/null; } 2>&1 )
  case "$err" in
    *"1 non-object element"*) return 0 ;;
    *) echo "    FAIL: expected a non-object-element warning on stderr, got: '${err}'" >&2; return 1 ;;
  esac
}

# Same guard on the reviews surface — the merge gate reads it, and a
# non-object element carries no verdict, so dropping it cannot mask one.
t_latest_review_by_survives_non_object_element() {
  MOCK_REVIEWS_BODY='["Not Found",{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-05-18T16:00:00Z","body":"ok","commit_id":"'"$HEAD_SHA"'"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]" 2>/dev/null)
  state=$(echo "$out" | jq -r '.state')
  assert_eq "verdict survives a non-object element" "APPROVED" "$state"
}

# End-to-end: the poll returns a snapshot instead of failing, which is what
# kept watch-pr-reviews.sh from stalling to budget on a degraded response.
t_main_survives_non_object_comment_element() {
  MOCK_MERGE_STATE=clean
  MOCK_COMMENTS_BODY='["Not Found",{"user":{"login":"Copilot"},"in_reply_to_id":null}]'
  local out copilot
  out=$(main "owner" "repo" "1" 2>/dev/null) || return 1
  copilot=$(echo "$out" | jq -r '.inline_comments.copilot')
  assert_eq "snapshot still produced, Copilot comment counted" "1" "$copilot"
}

# End-to-end through main() — the path the Step 7 merge gate actually reads.
# Codex and Copilot counts must not bleed into each other.
t_main_counts_copilot_comments_in_snapshot() {
  MOCK_MERGE_STATE=clean
  MOCK_COMMENTS_BODY='[{"user":{"login":"Copilot"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null},{"user":{"login":"github-actions[bot]"},"in_reply_to_id":null}]'
  local out counts
  out=$(main "owner" "repo" "1")
  counts=$(echo "$out" | jq -r '.inline_comments | "\(.codex)|\(.copilot)"')
  assert_eq "inline_comments {codex|copilot}" "2|1" "$counts"
}

# The resolver is variadic on login: a second policy-reviewer identity (a
# GitHub App, say) is resolved by passing it explicitly. These cases supply a
# hypothetical `policy-app[bot]` alongside the shipped login to prove the
# multi-login aggregation, without shipping that login in CODEX_REVIEW_LOGINS.
t_latest_review_by_resolves_extra_login() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"policy-app[bot]"},"state":"APPROVED","submitted_at":"2026-07-21T05:15:00Z"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "${CODEX_REVIEW_LOGINS[@]}" "policy-app[bot]")
  state=$(echo "$out" | jq -r '.state')
  assert_eq "extra-login review resolves to policy state" "APPROVED" "$state"
}

# Resolving across two logins must return the newest verdict when both appear.
t_latest_review_by_policy_reviewer_picks_newest_across_logins() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-07-21T05:00:00Z"},{"user":{"login":"policy-app[bot]"},"state":"CHANGES_REQUESTED","submitted_at":"2026-07-21T05:10:00Z"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "${CODEX_REVIEW_LOGINS[@]}" "policy-app[bot]")
  state=$(echo "$out" | jq -r '.state')
  assert_eq "newest policy verdict across both logins" "CHANGES_REQUESTED" "$state"
}

# Fail-safe: an active CHANGES_REQUESTED from one policy-reviewer identity must
# NOT be masked by a LATER clean review from the other identity — a gating
# watcher that surfaced the newest-overall would think the reviewer was clean
# while GitHub still blocked the merge.
t_latest_review_by_changes_requested_not_masked_by_later_other_login() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"CHANGES_REQUESTED","submitted_at":"2026-07-21T05:00:00Z"},{"user":{"login":"policy-app[bot]"},"state":"COMMENTED","submitted_at":"2026-07-21T05:10:00Z"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "${CODEX_REVIEW_LOGINS[@]}" "policy-app[bot]")
  state=$(echo "$out" | jq -r '.state')
  assert_eq "active block not masked by later clean review" "CHANGES_REQUESTED" "$state"
}

# An unknown login is NOT the policy reviewer: its review must not surface as
# .reviews.codex, and its comments must not count toward the reply gate.
t_main_ignores_unknown_login_review() {
  MOCK_MERGE_STATE=clean
  MOCK_REVIEWS_BODY='[{"user":{"login":"some-other-app[bot]"},"state":"APPROVED","submitted_at":"2026-07-21T05:15:00Z","commit_id":"'"$HEAD_SHA"'"}]'
  local out state
  out=$(main "owner" "repo" "1")
  state=$(echo "$out" | jq -r '.reviews.codex.state')
  assert_eq "unknown-login review is not the policy reviewer" "none" "$state"
}

t_toplevel_comments_by_ignores_unknown_login() {
  MOCK_COMMENTS_BODY='[{"user":{"login":"some-other-app[bot]"},"in_reply_to_id":null}]'
  local count
  count=$(toplevel_comments_by "owner" "repo" "1" "${CODEX_COMMENT_LOGINS[@]}")
  assert_eq "unknown-login inline comment not counted for policy reviewer" "0" "$count"
}

# A login's own latest review must be selected by submitted_at, NOT by the
# API's array position: jq group_by preserves input order within a group, so
# if the reviews API returns a login's reviews out of chronological order, a
# `last`-based pick would gate on a superseded verdict. Here the newest review
# (CHANGES_REQUESTED @18:00) is NOT last in the input — a correct max_by pick
# surfaces it; a `last` pick would wrongly return the APPROVED @16:00.
t_latest_review_by_picks_max_by_time_not_array_position() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"CHANGES_REQUESTED","submitted_at":"2026-07-25T18:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-07-25T17:00:00Z"},{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-07-25T16:00:00Z"}]'
  local out state submitted_at
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  submitted_at=$(echo "$out" | jq -r '.submitted_at')
  assert_eq "latest by time, not array position" "CHANGES_REQUESTED"     "$state"        || return 1
  assert_eq "latest submitted_at"                "2026-07-25T18:00:00Z"  "$submitted_at"
}

# GitHub review states outside the snapshot schema (DISMISSED, PENDING) are
# not live verdicts, but watch-pr-reviews.sh treats any non-"none" state as
# "a bot posted" and would let one satisfy the ready gate. They must normalize
# to "none".
t_latest_review_by_normalizes_dismissed_to_none() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"DISMISSED","submitted_at":"2026-07-25T16:00:00Z","body":"was dismissed"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  assert_eq "DISMISSED normalizes to none" "none" "$state"
}

t_latest_review_by_normalizes_pending_to_none() {
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"PENDING","submitted_at":"2026-07-25T16:00:00Z"}]'
  local out state
  out=$(latest_review_by "owner" "repo" "1" "github-actions[bot]")
  state=$(echo "$out" | jq -r '.state')
  assert_eq "PENDING normalizes to none" "none" "$state"
}

# --- #186: review verdicts bound to the PR head SHA ---

# A verdict on the current head passes through unchanged, flagged not-stale.
t_resolve_against_head_fresh_passes_through() {
  local review out state stale
  review='{"state":"APPROVED","submitted_at":"2026-07-25T10:00:00Z","body":"ok","commit_id":"'"$HEAD_SHA"'"}'
  out=$(resolve_review_against_head "$review" "$HEAD_SHA")
  state=$(echo "$out" | jq -r '.state')
  stale=$(echo "$out" | jq -r '.stale')
  assert_eq "fresh review keeps its state" "APPROVED" "$state" || return 1
  assert_eq "fresh review not stale"       "false"    "$stale"
}

# A verdict on a superseded SHA collapses to "none" (absent, not clean) with
# stale=true, but keeps its body/commit_id visible for diagnosis. This is the
# #186 fix: a stale APPROVED/COMMENTED must not read as a live clean verdict.
t_resolve_against_head_stale_collapses_to_none() {
  local review out state stale body commit
  review='{"state":"APPROVED","submitted_at":"2026-07-25T09:00:00Z","body":"reviewed old code","commit_id":"'"$OLD_SHA"'"}'
  out=$(resolve_review_against_head "$review" "$HEAD_SHA")
  state=$(echo "$out" | jq -r '.state')
  stale=$(echo "$out" | jq -r '.stale')
  body=$(echo "$out" | jq -r '.body')
  commit=$(echo "$out" | jq -r '.commit_id')
  assert_eq "stale review resolves to none" "none"                "$state"  || return 1
  assert_eq "stale review flagged stale"    "true"                "$stale"  || return 1
  assert_eq "stale verdict body kept"       "reviewed old code"   "$body"   || return 1
  assert_eq "stale verdict commit kept"     "$OLD_SHA"            "$commit"
}

# A genuinely-absent review (never posted) stays none, stale=false — an agent
# can tell "never reviewed" (stale false) from "reviewed older commit" (true).
t_resolve_against_head_none_stays_none_not_stale() {
  local review out state stale
  review='{"state":"none","submitted_at":null,"body":null,"commit_id":null}'
  out=$(resolve_review_against_head "$review" "$HEAD_SHA")
  state=$(echo "$out" | jq -r '.state')
  stale=$(echo "$out" | jq -r '.stale')
  assert_eq "absent review stays none"        "none"  "$state" || return 1
  assert_eq "absent review is not stale" "false" "$stale"
}

# End-to-end #186 symptom 2 (false ready): a stale COMMENTED from an earlier
# SHA must NOT surface as a live verdict — it collapses to none so the watcher
# keeps waiting instead of merging unreviewed code.
t_main_stale_review_collapses_to_none() {
  MOCK_MERGE_STATE=clean
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"COMMENTED","submitted_at":"2026-07-25T09:00:00Z","body":"looks fine","commit_id":"'"$OLD_SHA"'"}]'
  local out state stale
  out=$(main "owner" "repo" "1")
  state=$(echo "$out" | jq -r '.reviews.codex.state')
  stale=$(echo "$out" | jq -r '.reviews.codex.stale')
  assert_eq "stale codex verdict reads none in snapshot" "none" "$state" || return 1
  assert_eq "stale codex verdict flagged stale"          "true" "$stale"
}

# A fresh verdict (commit_id == head) surfaces normally.
t_main_fresh_review_surfaces_state() {
  MOCK_MERGE_STATE=clean
  MOCK_REVIEWS_BODY='[{"user":{"login":"github-actions[bot]"},"state":"APPROVED","submitted_at":"2026-07-25T10:00:00Z","body":"ok","commit_id":"'"$HEAD_SHA"'"}]'
  local out state stale
  out=$(main "owner" "repo" "1")
  state=$(echo "$out" | jq -r '.reviews.codex.state')
  stale=$(echo "$out" | jq -r '.reviews.codex.stale')
  assert_eq "fresh codex verdict surfaces" "APPROVED" "$state" || return 1
  assert_eq "fresh codex verdict not stale" "false"   "$stale"
}

t_main_surfaces_head_sha_top_level() {
  MOCK_MERGE_STATE=clean
  MOCK_REVIEWS_BODY='[]'
  local out head
  out=$(main "owner" "repo" "1")
  head=$(echo "$out" | jq -r '.head_sha')
  assert_eq "head_sha surfaced top-level" "$HEAD_SHA" "$head"
}

# Guard: an empty headRefOid must fail loudly, never silently void the review
# gate by marking every verdict stale.
t_main_no_head_sha_fails() {
  MOCK_MERGE_STATE=no-head
  MOCK_REVIEWS_BODY='[]'
  # Subshell: main() calls `exit` on the guard, which would otherwise
  # terminate this sourced test script rather than just the call.
  local rc=0
  ( main "owner" "repo" "1" ) >/dev/null 2>&1 || rc=$?
  [[ "$rc" -ne 0 ]] || { echo "    FAIL: main should exit non-zero on empty headRefOid, got rc=0" >&2; return 1; }
}

# --- #182: cancelled superseded runs are no-signal, not failure ---

# A cancelled bucket alongside a success is still success (the cancelled twin
# was superseded; its replacement concluded). Folding cancel into pending would
# have wedged this.
t_ci_status_cancel_with_success_is_success() {
  MOCK_MERGE_STATE=clean
  MOCK_CHECKS_BODY='[{"name":"CI","bucket":"success"},{"name":"CI","bucket":"cancel"}]'
  local out ci
  out=$(main "owner" "repo" "1")
  ci=$(echo "$out" | jq -r '.ci.status')
  assert_eq "success alongside a cancelled twin is success" "success" "$ci"
}

# Only cancels present (replacement not yet registered) → pending, so the
# watcher waits rather than concluding.
t_ci_status_only_cancels_is_pending() {
  MOCK_MERGE_STATE=clean
  MOCK_CHECKS_BODY='[{"name":"CI","bucket":"cancel"},{"name":"review","bucket":"cancel"}]'
  local out ci
  out=$(main "owner" "repo" "1")
  ci=$(echo "$out" | jq -r '.ci.status')
  assert_eq "all-cancelled reads pending" "pending" "$ci"
}

# A real failure on head still fails, even next to a cancelled run.
t_ci_status_fail_with_cancel_is_failure() {
  MOCK_MERGE_STATE=clean
  MOCK_CHECKS_BODY='[{"name":"CI","bucket":"fail"},{"name":"old","bucket":"cancel"}]'
  local out ci
  out=$(main "owner" "repo" "1")
  ci=$(echo "$out" | jq -r '.ci.status')
  assert_eq "a real fail still fails next to a cancel" "failure" "$ci"
}

# --- driver ---

echo "== poll-pr-reviews.sh tests =="
run "fetch_merge_state returns {CLEAN, MERGEABLE} for a clean PR"     t_fetch_merge_state_clean_returns_mergeable_envelope
run "fetch_merge_state returns {DIRTY, CONFLICTING} on conflict"      t_fetch_merge_state_dirty_returns_conflicting_envelope
run "fetch_merge_state propagates UNKNOWN/UNKNOWN while computing"    t_fetch_merge_state_unknown_returns_unknown_envelope
run "main surfaces merge_state as a top-level field"                  t_main_surfaces_merge_state_as_top_level_field
run "main propagates DIRTY merge_state end-to-end"                    t_main_propagates_dirty_state
run "latest_review_by picks newest review on page 2 (issue #83)"      t_latest_review_by_picks_from_last_page
run "latest_review_by returns 'none' for empty reviews"               t_latest_review_by_returns_none_when_no_reviews
run "latest_review_by surfaces the review body text"                  t_latest_review_by_surfaces_body_text
run "latest_review_by ignores other logins across pages"              t_latest_review_by_filters_other_logins_across_pages
run "toplevel_comments_by sums counts across pages (issue #83)"       t_toplevel_comments_by_sums_across_pages
run "toplevel_comments_by returns 0 for empty comments"               t_toplevel_comments_by_returns_zero_for_no_comments
run "toplevel_comments_by counts the 'Copilot' comment login"         t_toplevel_comments_by_counts_copilot_login
run "toplevel_comments_by matches either Copilot login"               t_toplevel_comments_by_matches_either_copilot_login
run "toplevel_comments_by excludes replies and foreign logins"        t_toplevel_comments_by_excludes_replies_and_other_logins
run "main counts Copilot comments in the snapshot"                    t_main_counts_copilot_comments_in_snapshot
run "toplevel_comments_by survives a non-object element (#300)"       t_toplevel_comments_by_survives_non_object_element
run "toplevel_comments_by warns about the dropped element (#300)"     t_toplevel_comments_by_warns_on_non_object_element
run "latest_review_by survives a non-object element (#300)"           t_latest_review_by_survives_non_object_element
run "main returns a snapshot despite a non-object element (#300)"     t_main_survives_non_object_comment_element
run "latest_review_by resolves an explicitly passed extra login"      t_latest_review_by_resolves_extra_login
run "latest_review_by picks newest policy verdict across logins"      t_latest_review_by_policy_reviewer_picks_newest_across_logins
run "latest_review_by never masks an active block with a later clean" t_latest_review_by_changes_requested_not_masked_by_later_other_login
run "latest_review_by picks latest by time, not array position"       t_latest_review_by_picks_max_by_time_not_array_position
run "latest_review_by normalizes DISMISSED to none"                   t_latest_review_by_normalizes_dismissed_to_none
run "latest_review_by normalizes PENDING to none"                     t_latest_review_by_normalizes_pending_to_none
run "main does not surface an unknown login as .reviews.codex"        t_main_ignores_unknown_login_review
run "toplevel_comments_by ignores an unknown comment login"           t_toplevel_comments_by_ignores_unknown_login
run "resolve_review_against_head: fresh verdict passes through"       t_resolve_against_head_fresh_passes_through
run "resolve_review_against_head: stale verdict collapses to none"    t_resolve_against_head_stale_collapses_to_none
run "resolve_review_against_head: absent stays none, not stale"       t_resolve_against_head_none_stays_none_not_stale
run "main collapses a stale review to none (#186 false ready)"        t_main_stale_review_collapses_to_none
run "main surfaces a fresh (head-bound) review's state"               t_main_fresh_review_surfaces_state
run "main surfaces head_sha as a top-level field"                     t_main_surfaces_head_sha_top_level
run "main fails loudly on an empty headRefOid"                        t_main_no_head_sha_fails
run "ci.status: success next to a cancelled twin is success (#182)"   t_ci_status_cancel_with_success_is_success
run "ci.status: only-cancels reads pending (#182)"                    t_ci_status_only_cancels_is_pending
run "ci.status: a real fail next to a cancel still fails (#182)"      t_ci_status_fail_with_cancel_is_failure

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
