#!/usr/bin/env bash
# Dismiss a gating bot's CHANGES_REQUESTED review once the SAME bot has
# posted a later non-CHANGES_REQUESTED verdict on the PR.
#
# Why this exists: the policy reviewer runs as `github-actions[bot]` (the Codex CLI review workflow, review-codex.yml,
# submits with the workflow token), which GitHub rejects APPROVE from with HTTP
# 422. A clean re-review is therefore a COMMENT, and a later COMMENT never
# supersedes an earlier CHANGES_REQUESTED in GitHub's merge-gating model — the
# stale CHANGES_REQUESTED keeps blocking the merge until it is dismissed. The
# operator running the release skill dismisses it here. The decision is fully
# deterministic, so it is a script, not agent judgment (rules/script-delegation.md).
#
# Decision, per gating bot (see GATING_BOTS below):
#   - latest review state == CHANGES_REQUESTED => leave it; the bot is
#     currently requesting changes, nothing to dismiss.
#   - latest review state == COMMENTED or APPROVED (a fresh all-clear) =>
#     dismiss every EARLIER review from that bot still in CHANGES_REQUESTED.
#   - any other latest state (DISMISSED, PENDING) => no-op; a dismissed or
#     pending latest review is NOT an all-clear, so an earlier active
#     CHANGES_REQUESTED that no all-clear superseded must stay put.
# Reviews already in DISMISSED state are skipped, so re-running is a no-op
# (idempotent per rules/file-hygiene.md).
#
# Usage: dismiss-stale-reviews.sh <owner> <repo> <pr-number>
# Out:   one JSON object on stdout:
#   {
#     "pr_number": N,
#     "dismissed": [{"login": "...", "review_id": N, "commit_id": "..."}],
#     "left_active": [{"login": "...", "review_id": N}]  // latest is still CHANGES_REQUESTED
#   }
# Exit:  0 on successful query (including no-op); non-zero with a stderr
#        diagnostic on API/argument failure.

set -euo pipefail

# The bot logins whose CHANGES_REQUESTED reviews gate the merge — the same
# two the release skill polls (skills/release/poll-pr-reviews.sh). An array
# iterated with quoted expansion, NOT a space-string: the `[bot]` suffix is
# a glob bracket, so an unquoted `for login in $GATING_BOTS` would rewrite
# the token against a matching filename in the release checkout (e.g.
# `github-actionsb`) and silently miss the review.
GATING_BOTS=("github-actions[bot]" "copilot-pull-request-reviewer[bot]")

# Fixed dismissal message — a dismissal records who/why on the PR timeline.
DISMISS_MESSAGE="Superseded by a later all-clear review from the same bot — dismissed by the release skill so the stale request stops gating the merge."

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# All reviews by one login, chronological (GitHub returns ascending by
# submitted_at). `--paginate` is mandatory so a PR with >30 reviews doesn't
# truncate and hide the latest verdict — same discipline as
# poll-pr-reviews.sh.
reviews_by() {
  local owner="$1" repo="$2" pr="$3" login="$4"
  gh api --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" \
    | jq -s --arg login "$login" \
        '(add // []) | [.[] | select(.user.login == $login) | {id, state, commit_id, submitted_at}]'
}

dismiss_review() {
  local owner="$1" repo="$2" pr="$3" review_id="$4"
  gh api -X PUT \
    "repos/${owner}/${repo}/pulls/${pr}/reviews/${review_id}/dismissals" \
    -f message="$DISMISS_MESSAGE" \
    -f event="DISMISS" >/dev/null
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr="$3"
  if [[ -z "$owner" || -z "$repo" || -z "$pr" ]]; then
    echo "error: <owner>, <repo>, and <pr-number> are all required and must be non-empty" >&2
    exit 2
  fi

  local dismissed="[]" left_active="[]"
  local login reviews latest_state stale

  for login in "${GATING_BOTS[@]}"; do
    reviews=$(reviews_by "$owner" "$repo" "$pr" "$login") \
      || { echo "error: failed to fetch reviews for ${login} on ${owner}/${repo}#${pr} — run 'gh auth status' to verify auth, then retry" >&2; exit 1; }

    # No reviews from this bot yet — nothing to do.
    [[ "$(jq 'length' <<<"$reviews")" == "0" ]] && continue

    latest_state=$(jq -r '.[-1].state' <<<"$reviews")

    # The bot's current verdict is still CHANGES_REQUESTED: leave it gating.
    if [[ "$latest_state" == "CHANGES_REQUESTED" ]]; then
      left_active=$(jq --arg login "$login" \
        '. + [{login: $login, review_id: '"$(jq '.[-1].id' <<<"$reviews")"'}]' <<<"$left_active")
      continue
    fi

    # Dismissal requires a fresh all-clear from the same bot. Only COMMENTED
    # (a bot cannot APPROVE — HTTP 422) or APPROVED counts. A latest state
    # of DISMISSED or PENDING is NOT an all-clear, so an earlier active
    # CHANGES_REQUESTED that no all-clear superseded must stay put.
    if [[ "$latest_state" != "COMMENTED" && "$latest_state" != "APPROVED" ]]; then
      continue
    fi

    # Latest is an all-clear — dismiss every EARLIER review still in
    # CHANGES_REQUESTED (already-DISMISSED ones are excluded, so re-runs
    # are no-ops).
    stale=$(jq -c '.[:-1][] | select(.state == "CHANGES_REQUESTED")' <<<"$reviews")
    [[ -z "$stale" ]] && continue

    local row rid cid
    while IFS= read -r row; do
      [[ -z "$row" ]] && continue
      rid=$(jq -r '.id' <<<"$row")
      cid=$(jq -r '.commit_id' <<<"$row")
      dismiss_review "$owner" "$repo" "$pr" "$rid" \
        || { echo "error: failed to dismiss review ${rid} by ${login} on ${owner}/${repo}#${pr}" >&2; exit 1; }
      dismissed=$(jq --arg login "$login" --arg cid "$cid" \
        '. + [{login: $login, review_id: '"$rid"', commit_id: $cid}]' <<<"$dismissed")
    done <<<"$stale"
  done

  jq -n \
    --argjson pr "$pr" \
    --argjson dismissed "$dismissed" \
    --argjson left_active "$left_active" \
    '{pr_number: $pr, dismissed: $dismissed, left_active: $left_active}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
