#!/usr/bin/env bash
# Dismiss a gating bot's CHANGES_REQUESTED review once the SAME review lane
# has posted a later non-CHANGES_REQUESTED verdict on the PR.
#
# Why this exists: the policy reviewer runs as `github-actions[bot]` (the Codex CLI review workflow, review-codex.yml,
# submits with the workflow token), which GitHub rejects APPROVE from with HTTP
# 422. A clean re-review is therefore a COMMENT, and a later COMMENT never
# supersedes an earlier CHANGES_REQUESTED in GitHub's merge-gating model — the
# stale CHANGES_REQUESTED keeps blocking the merge until it is dismissed. The
# operator running the release skill dismisses it here. The decision is fully
# deterministic, so it is a script, not agent judgment (rules/script-delegation.md).
#
# "Same reviewer" means same review LANE, not same login: a Claude quality
# review can post under the Codex login, and its clean COMMENT must never
# dismiss a blocking Codex review (docs/adr/0010-review-lanes.md). Every review
# from a GATING_BOTS login is classified by skills/release/review-lanes.sh
# (login plus body marker; unmarked legacy Codex reviews fall back to the
# codex-policy lane by login).
#
# Decision, per lane, over that lane's reviews ordered by submitted_at:
#   - latest review state == CHANGES_REQUESTED => leave it; the lane is
#     currently requesting changes, nothing to dismiss.
#   - latest review state == COMMENTED or APPROVED (a fresh all-clear) =>
#     dismiss every EARLIER review in that lane still in CHANGES_REQUESTED.
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
#     "dismissed": [{"login": "...", "lane": "...", "review_id": N, "commit_id": "..."}],
#     "left_active": [{"login": "...", "lane": "...", "review_id": N}]  // latest is still CHANGES_REQUESTED
#   }
# Exit:  0 on successful query (including no-op); non-zero with a stderr
#        diagnostic on API/argument failure.

set -euo pipefail

# shellcheck source=skills/release/review-lanes.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/review-lanes.sh" \
  || { echo "error: cannot source review-lanes.sh beside dismiss-stale-reviews.sh — reinstall the release skill so both files ship together" >&2; exit 2; }

# The bot logins whose CHANGES_REQUESTED reviews gate the merge — every login
# a review lane can carry (review-lanes.sh). An array iterated with quoted
# expansion, NOT a space-string: the `[bot]` suffix is a glob bracket.
GATING_BOTS=("${CODEX_POLICY_LOGINS[@]}" "${CLAUDE_QUALITY_LOGINS[@]}")

# Fixed dismissal message — a dismissal records who/why on the PR timeline.
DISMISS_MESSAGE="Superseded by a later all-clear review from the same bot — dismissed by the release skill so the stale request stops gating the merge."

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# Every GATING_BOTS review, classified into its lane and ordered by
# submitted_at. `--paginate` is mandatory so a PR with >30 reviews doesn't
# truncate and hide the latest verdict — same discipline as
# poll-pr-reviews.sh. Reviews no lane claims are dropped.
lane_reviews() {
  local owner="$1" repo="$2" pr="$3" logins cfg
  logins=$(jq -n '$ARGS.positional' --args "${GATING_BOTS[@]}") || return 1
  cfg=$(review_lanes_config) || return 1
  gh api --hostname github.com --paginate "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" \
    | jq -s --argjson logins "$logins" --argjson cfg "$cfg" "$(review_lanes_jq)"'
        (add // []) | [.[] | select(type == "object") | select((.user.login // "") | IN($logins[]))
          | {id, state, commit_id, submitted_at, login: .user.login, lane: review_lane($cfg)}
          | select(.lane != null)]
        | sort_by(.submitted_at)'
}

dismiss_review() {
  local owner="$1" repo="$2" pr="$3" review_id="$4"
  gh api --hostname github.com -X PUT \
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
  local all lane reviews latest_state stale

  all=$(lane_reviews "$owner" "$repo" "$pr") \
    || { echo "error: failed to fetch reviews on ${owner}/${repo}#${pr} — run 'gh auth status --hostname github.com' to verify auth, then retry" >&2; exit 1; }

  for lane in codex-policy claude-quality; do
    reviews=$(jq --arg lane "$lane" '[.[] | select(.lane == $lane)]' <<<"$all")

    # No reviews in this lane yet — nothing to do.
    [[ "$(jq 'length' <<<"$reviews")" == "0" ]] && continue

    latest_state=$(jq -r '.[-1].state' <<<"$reviews")

    # The lane's current verdict is still CHANGES_REQUESTED: leave it gating.
    if [[ "$latest_state" == "CHANGES_REQUESTED" ]]; then
      left_active=$(jq --argjson r "$(jq '.[-1]' <<<"$reviews")" \
        '. + [{login: $r.login, lane: $r.lane, review_id: $r.id}]' <<<"$left_active")
      continue
    fi

    # Dismissal requires a fresh all-clear from the same lane. Only COMMENTED
    # (a bot cannot APPROVE — HTTP 422) or APPROVED counts. A latest state
    # of DISMISSED or PENDING is NOT an all-clear, so an earlier active
    # CHANGES_REQUESTED that no all-clear superseded must stay put.
    if [[ "$latest_state" != "COMMENTED" && "$latest_state" != "APPROVED" ]]; then
      continue
    fi

    # Latest is an all-clear — dismiss every EARLIER review in the lane still
    # in CHANGES_REQUESTED (already-DISMISSED ones are excluded, so re-runs
    # are no-ops).
    stale=$(jq -c '.[:-1][] | select(.state == "CHANGES_REQUESTED")' <<<"$reviews")
    [[ -z "$stale" ]] && continue

    local row rid
    while IFS= read -r row; do
      [[ -z "$row" ]] && continue
      rid=$(jq -r '.id' <<<"$row")
      dismiss_review "$owner" "$repo" "$pr" "$rid" \
        || { echo "error: failed to dismiss review ${rid} (${lane} lane) on ${owner}/${repo}#${pr}" >&2; exit 1; }
      dismissed=$(jq --argjson r "$row" \
        '. + [{login: $r.login, lane: $r.lane, review_id: $r.id, commit_id: $r.commit_id}]' <<<"$dismissed")
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
