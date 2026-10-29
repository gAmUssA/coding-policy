#!/usr/bin/env bash
# Snapshot CI status, bot review states + bodies, and inline comment counts for a PR.
# Non-blocking — call repeatedly to observe transitions.
#
# Usage: poll-pr-reviews.sh <owner> <repo> <pr-number>
# Out:   one JSON object on stdout with the schema below.
# Exit:  0 on successful query; non-zero with stderr diagnostic on failure
#
# `reviews.*.body` carries the full review body text — a review's state classifies
# whether it gates the merge, not whether its body must be read. A COMMENTED review
# with zero inline comments still carries a body (see rules/reviewer-feedback-reading.md).
#
# Schema:
#   {
#     "pr_number": N,
#     "head_sha": "<PR head commit SHA>",
#     "ci":   {"status": "pending|success|failure|none", "checks": [...]},
#     "reviews": {
#       "codex":   {"state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#                   "submitted_at": "ISO-8601|null", "body": "text|null",
#                   "commit_id": "<SHA the review is bound to>|null",
#                   "stale": bool, "requested": bool}
#     },
#     "lanes": {   // every review lane (skills/release/review-lanes.sh)
#       "codex-policy":   {<same fields as reviews.codex>,   "gating": bool, "expected": true},
#       "claude-quality": {state, submitted_at, body, commit_id, stale, "gating": bool,
#                          "expected": bool}
#     },
#     "other_reviews": [   // active verdict per login no lane claims, oldest first
#       {"login": "...", "is_bot": bool,
#        "state": "APPROVED|CHANGES_REQUESTED|COMMENTED|none",
#        "submitted_at": "ISO-8601", "body": "text|null",
#        "commit_id": "<SHA>|null", "stale": bool}
#     ],
#     "inline_comments": {"codex": N, "other": N},
#     "merge_state": {"status": "CLEAN|DIRTY|BLOCKED|BEHIND|UNSTABLE|...",
#                     "mergeable": "MERGEABLE|CONFLICTING|UNKNOWN"}
#   }
#
# Each verdict is read per review LANE (login plus body marker, see
# review-lanes.sh), never per login: `reviews.codex` is the codex-policy lane
# only, so a Claude-lane review posted under the same login never becomes the
# Codex verdict. `lanes.*.gating` says which lanes gate the merge. A review
# no lane claims (a human, or Copilot where a consumer repo still enables it)
# is reported under `other_reviews` so its body is still read
# (rules/reviewer-feedback-reading.md); no lane gate reads it
# (docs/adr/0014-retire-copilot-lane.md). An `other_reviews` entry is NOT
# collapsed against head: GitHub keeps a human CHANGES_REQUESTED active
# across pushes until that login APPROVES or it is dismissed, and a later
# COMMENTED review (a thread reply is one) never clears it. `state` is that
# active verdict: the login's newest APPROVED / CHANGES_REQUESTED / DISMISSED
# review decides it (DISMISSED reads "none"), else COMMENTED when the login
# only commented. `body`, `submitted_at` and `commit_id` come from the review
# that decided it; `stale` only says that review predates head.
#
# A lane is `expected` when its verdict is owed on this head: the codex-policy
# lane always; a lane with a `workflow` in review-lanes.sh when a check from
# that workflow is pass, fail or pending on head. The watcher waits for every
# gating or expected lane.
# `inline_comments.other` counts top-level inline comments from every author
# outside CODEX_COMMENT_LOGINS, each of which needs a reply before merge.
#
# A review's `.state` is resolved AGAINST `head_sha`: a review whose
# `commit_id` is not the current head has not reviewed the current code, so
# its `.state` collapses to "none" (absent, not clean) and `.stale` is true —
# the rule in rules/review-severity.md / rules/ship-on-green.md that a verdict
# approves only the commit it reviewed. The pre-resolution verdict stays
# visible in `submitted_at` / `body` / `commit_id` so a reader can tell "never
# reviewed" (state none, stale false) from "reviewed an older commit" (state
# none, stale true). Binding both symptoms to head fixes them together: a
# stale CHANGES_REQUESTED no longer false-reds a fix no reviewer has seen, and
# a stale COMMENTED/APPROVED no longer false-readies unreviewed code (#186).
#
# `requested` reports exactly one fact: a review request for that login is still
# pending on the PR (#369). It is NOT a "has this review been triggered" flag.
# A delivered review clears its own request, so `requested` is false once
# `state` is set. The policy reviewer is PUSH-triggered — review-codex.yml runs
# on the push, never on a request — so its `requested` is false on every PR
# unless someone requests it by hand, and an in-flight policy review reads
# `state: "none", requested: false`. Resolve a reviewer's arrival by how it is
# triggered (rules/ci-safety.md Always Watch CI), never by this field alone.
#
# `merge_state.status == "DIRTY"` / `mergeable == "CONFLICTING"` means GitHub
# couldn't create `refs/pull/N/merge` and silently skipped `pull_request:`
# workflows — agent should surface a rebase recommendation rather than keep
# polling `ci.status: none`.

set -euo pipefail

# Bot logins, by surface. A reviewer does not necessarily author its reviews
# and its inline comments under the same login, so each surface keeps its own
# login set:
#
#   surface          Policy reviewer
#   ---------------  ---------------------
#   review           github-actions[bot]
#   inline comment   github-actions[bot]
#
# The policy reviewer is the review-codex.yml workflow (Codex CLI) submitting
# with the workflow's GITHUB_TOKEN, so its author is `github-actions[bot]` in
# every repo — this plugin's own and each onboarded consumer. The login sets
# stay ARRAYS so a second identity can be added without touching the
# resolver; `latest_review_by` aggregates fail-safe across every login it is
# given (each login's own latest, CHANGES_REQUESTED wins).
#
# Comment counting matches a SET of logins per reviewer: counting against the
# wrong login reads 0 on every PR, which vacuously satisfies the release
# skill's Step 7 "every inline comment has a reply" merge gate. A comment
# carries exactly one author, so listing multiple logins cannot double-count.
#
# Review logins come from the lane classifier (review-lanes.sh): one login can
# carry several lanes, so main() reads each verdict per LANE, never per login.
# shellcheck source=skills/release/review-lanes.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/review-lanes.sh" \
  || { echo "error: cannot source review-lanes.sh beside poll-pr-reviews.sh — reinstall the release skill so both files ship together" >&2; exit 2; }
CODEX_REVIEW_LOGINS=("${CODEX_POLICY_LOGINS[@]}")
CODEX_COMMENT_LOGINS=("github-actions[bot]")

# Slurp a paginated `gh api` array into one JSON array, dropping any element
# that is not an object. A degraded or partial response can carry a string
# element where an object belongs; indexing it (`.in_reply_to_id`,
# `.user.login`) aborts jq mid-filter and fails the whole poll, which
# watch-pr-reviews.sh then burns its full attempt budget on and reports as
# `pending_at_budget` — "no reviewer posted" when the truth is "the tool
# broke" (issue #300). A non-object element carries neither a verdict nor a
# comment, so dropping it loses nothing; the warning keeps the drop visible
# rather than silent (rules/error-handling.md — best-effort work that
# continues past a failure warns, never nothing).
#
# `--paginate` is mandatory: GitHub's default per-page is 30, and a PR with
# more than that many reviews/comments would otherwise return only the first
# page. `--jq` is incompatible with `--paginate` here (it applies per page,
# not across the stream), so slurp every page into one array with `jq -s`
# before filtering. `per_page=100` is the API maximum and keeps request
# volume bounded.
slurp_api_array() {
  local endpoint="$1" what="$2" payload malformed
  payload=$(gh api --hostname github.com --paginate "$endpoint" | jq -s '(add // [])') \
    || { echo "error: failed to fetch ${what} from ${endpoint}" >&2; return 1; }
  malformed=$(printf '%s' "$payload" | jq '[.[] | select(type != "object")] | length') \
    || { echo "error: failed to inspect the ${what} payload from ${endpoint}" >&2; return 1; }
  if (( malformed > 0 )); then
    echo "warning: ${endpoint} returned ${malformed} non-object element(s) in the ${what} payload — dropped; re-run poll-pr-reviews.sh if the snapshot looks wrong, and inspect the response with 'gh api --hostname github.com --paginate ${endpoint}' if it persists" >&2
  fi
  printf '%s' "$payload" | jq '[.[] | select(type == "object")]'
}

# Missing a page would let the `last` filter pick the last entry on page 1 —
# not the actual latest review — and the gate could approve a merge against
# stale data; slurp_api_array above owns the pagination.
# Variadic on login so one reviewer's multiple identities collapse to a single
# verdict (see the login table above). Today the policy reviewer has one login,
# but this is a MERGE GATE: aggregate fail-safe rather than assume. Take each login's
# OWN latest review, then if ANY of those is CHANGES_REQUESTED surface that
# (an active block from one identity must never be masked by a later clean
# review from another); otherwise surface the newest among them.
# `max_by(.submitted_at)` for the per-login latest, NOT `last`: jq `group_by`
# sorts only by the group key and preserves input order within a group, so
# `last` returns the newest review only when the API happened to return that
# login's reviews in chronological order. A merge gate must not rest on that
# assumption — a login whose re-review came back out of order would gate on a
# superseded verdict — so select the max by submitted_at explicitly.
# Aggregation over an already-filtered review array, shared by
# latest_review_by and latest_review_in_lane.
# shellcheck disable=SC2016 # jq program, not shell expansions
LATEST_REVIEW_JQ='
        | (group_by(.user.login) | map(max_by(.submitted_at))) as $per_login_latest
        | ( ($per_login_latest | map(select(.state == "CHANGES_REQUESTED")) | first)
            // ($per_login_latest | sort_by(.submitted_at) | last) )
        # Normalize to the documented schema states. GitHub also emits
        # DISMISSED and PENDING; neither is a live verdict, but the watcher
        # treats any non-"none" state as "a bot posted" and would let a
        # dismissed/pending review satisfy the ready gate. Collapse anything
        # outside {APPROVED, CHANGES_REQUESTED, COMMENTED} to "none" (absent),
        # keeping submitted_at/body/commit_id visible for diagnosis.
        | if . == null then {state: "none", submitted_at: null, body: null, commit_id: null}
          else {state: (if (.state | IN("APPROVED", "CHANGES_REQUESTED", "COMMENTED")) then .state else "none" end),
                submitted_at, body, commit_id} end'

latest_review_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for review lookup" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" "review" \
    | jq --argjson logins "$logins_json" "[.[] | select(.user.login | IN(\$logins[]))] ${LATEST_REVIEW_JQ}"
}

# The same verdict, restricted to one review LANE (review-lanes.sh): a review
# counts only when the classifier puts it in <lane>, so a Claude-lane comment
# under the Codex login never stands in for the Codex verdict (ADR 0010).
latest_review_in_lane() {
  local owner="$1" repo="$2" pr="$3" lane="$4" cfg
  cfg=$(review_lanes_config) \
    || { echo "error: failed to build the review-lane config" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" "review" \
    | jq --argjson cfg "$cfg" --arg lane "$lane" \
        "$(review_lanes_jq) [.[] | select(review_lane(\$cfg) == \$lane)] ${LATEST_REVIEW_JQ}"
}

# Resolve a latest_review_by result against the PR head SHA. A review's verdict
# approves only the commit it reviewed (rules/ship-on-green.md), so a
# review whose `commit_id` is not the head collapses to state "none" — absent,
# not clean — while its verdict stays visible for diagnosis under a `stale`
# flag. `head` is always the live headRefOid in production (main asserts it
# non-empty); an empty head would mark every real review stale, which is why
# main refuses to proceed without one rather than silently voiding the gate.
#
# NOTE on ordering vs the CHANGES_REQUESTED-wins aggregation in
# latest_review_by: today exactly one policy-reviewer identity posts per PR, so
# aggregation collapses to that one review and resolving here is equivalent to
# resolving before aggregation. The hypothetical mixed-freshness-two-logins PR
# does not occur (see the login table above); if it ever does, fold this
# binding into the jq above so stale reviews drop before the CR-wins pick.
resolve_review_against_head() {
  local review="$1" head="$2"
  printf '%s' "$review" | jq --arg head "$head" '
    if .state == "none" then . + {stale: false}
    elif .commit_id == $head then . + {stale: false}
    else {state: "none", submitted_at, body, commit_id, stale: true} end'
}

# Count top-level (non-reply) inline comments authored by ANY of <login...>.
# Variadic on login so one reviewer's multiple author identities collapse to a
# single count — see the login table at the top of this file.
toplevel_comments_by() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for comment count" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/comments?per_page=100" "inline comment" \
    | jq --argjson logins "$logins_json" '
        [.[] | select(.in_reply_to_id == null) | select(.user.login | IN($logins[]))]
        | length'
}

# Active verdict per login for every review no lane claims (review-lanes.sh),
# with the author's bot/human type, resolved the way GitHub resolves a
# reviewer's state (see the header): a later COMMENTED never clears an earlier
# CHANGES_REQUESTED. `stale` marks a review bound to an older commit without
# collapsing its state.
unlaned_reviews() {
  local owner="$1" repo="$2" pr="$3" head="$4" cfg
  cfg=$(review_lanes_config) \
    || { echo "error: failed to build the review-lane config" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/reviews?per_page=100" "review" \
    | jq --argjson cfg "$cfg" --arg head "$head" \
        "$(review_lanes_jq)"' [.[] | select(review_lane($cfg) == null) | select((.user.login // "") != "")]
          | group_by(.user.login)
          | map( (map(select(.state | IN("APPROVED", "CHANGES_REQUESTED", "DISMISSED"))) | max_by(.submitted_at)) as $decisive
                 | (map(select(.state == "COMMENTED")) | max_by(.submitted_at)) as $commented
                 | (if $decisive != null and ($decisive.state | IN("APPROVED", "CHANGES_REQUESTED")) then $decisive
                    elif $commented != null then $commented
                    else ($decisive // (max_by(.submitted_at))) | .state = "none" end) as $v
                 | {login: $v.user.login, is_bot: ($v.user.type == "Bot"),
                    state: (if ($v.state | IN("APPROVED", "CHANGES_REQUESTED", "COMMENTED")) then $v.state else "none" end),
                    submitted_at: $v.submitted_at, body: $v.body, commit_id: $v.commit_id,
                    stale: ($v.commit_id != $head)})
          | sort_by(.submitted_at)'
}

# Count top-level (non-reply) inline comments authored by NONE of <login...>.
toplevel_comments_excluding() {
  local owner="$1" repo="$2" pr="$3"; shift 3
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") \
    || { echo "error: failed to encode login list for comment count" >&2; return 1; }
  slurp_api_array "repos/${owner}/${repo}/pulls/${pr}/comments?per_page=100" "inline comment" \
    | jq --argjson logins "$logins_json" '
        [.[] | select(.in_reply_to_id == null) | select((.user.login // "") | IN($logins[]) | not)]
        | length'
}

# Also carries the PR head SHA (headRefOid) so review verdicts can be bound to
# the commit they reviewed — folded into this call rather than a second
# `gh pr view` so head and merge-state come from one consistent read. main
# splits head_sha to a top-level field and keeps merge_state as {status,
# mergeable}.
fetch_merge_state() {
  local owner="$1" repo="$2" pr="$3"
  gh pr view "$pr" --repo "github.com/${owner}/${repo}" --json mergeStateStatus,mergeable,headRefOid \
    | jq -c '{status: .mergeStateStatus, mergeable: .mergeable, head_sha: .headRefOid}'
}

# Logins with a review request still pending on the PR, lowercased and with the
# `[bot]` suffix stripped so one spelling compares against another. GraphQL, not
# the REST `requested_reviewers` endpoint: that endpoint omits bot reviewers
# entirely (#276), so every bot lane would read "never requested" there.
#
# Owner, repo and PR number travel as GraphQL variables, never interpolated
# into the query text. `-F` types the number as `Int!`; it would also read a
# file for an `@`-prefixed value, so the number is validated first.
# shellcheck disable=SC2016 # `$owner`, `$repo`, `$pr` are GraphQL variables, not shell expansions
REQUESTED_LOGINS_QUERY='
  query($owner: String!, $repo: String!, $pr: Int!) {
    repository(owner: $owner, name: $repo) {
      pullRequest(number: $pr) {
        reviewRequests(first: 50) { nodes { requestedReviewer {
          __typename
          ... on Bot { login }
          ... on User { login }
          ... on Team { slug }
        } } }
      }
    }
  }'

fetch_requested_logins() {
  local owner="$1" repo="$2" pr="$3"
  if [[ ! "$pr" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: pr-number must be a positive integer; got '${pr}'" >&2
    return 1
  fi
  gh api --hostname github.com graphql -f query="$REQUESTED_LOGINS_QUERY" -f owner="$owner" -f repo="$repo" -F pr="$pr" \
    --jq '[.data.repository.pullRequest.reviewRequests.nodes[]?.requestedReviewer
           | (.login // .slug) | select(. != null) | ascii_downcase | sub("\\[bot\\]$"; "")]' \
    | jq -c '.'
}

# Is any of <login...> among the pending review requests?
requested_among() { # <requested-json> <login...>
  local requested="$1"; shift
  local logins_json
  logins_json=$(jq -n '$ARGS.positional' --args "$@") || return 1
  printf '%s' "$requested" | jq --argjson logins "$logins_json" \
    '[$logins[] | ascii_downcase | sub("\\[bot\\]$"; "")] as $want
     | any(.[]; . as $have | $want | index($have) != null)'
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr_number="$3"
  if [[ ! "$pr_number" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: pr-number must be a positive integer; got '${pr_number}' — pass the PR's number, e.g. '$0 ${owner} ${repo} 42'" >&2
    exit 2
  fi

  # gh pr checks exits 8 when no checks are configured — distinguish that from real errors.
  local checks_json checks_raw rc=0
  checks_raw=$(gh pr checks "$pr_number" --repo "github.com/${owner}/${repo}" --json name,bucket,workflow 2>&1) || rc=$?
  if [[ $rc -eq 0 ]]; then
    checks_json="$checks_raw"
  # Here-string, not `echo | grep -qi`: `-q` makes grep exit at the first
  # match and close the pipe on a still-writing echo, so under `pipefail` the
  # pipeline can carry echo's SIGPIPE (141) while grep matched — turning this
  # no-checks branch false and routing a valid state into the error exit
  # below (rules/error-handling.md Shell Error Handling).
  elif [[ $rc -eq 8 ]] || grep -qi "no check" <<<"$checks_raw"; then
    checks_json='[]'
  else
    echo "error: gh pr checks ${pr_number} --repo github.com/${owner}/${repo} failed (rc=${rc}): ${checks_raw}" >&2
    exit 1
  fi

  # `cancel` is NOT failure. Pushing a fix to an open PR auto-cancels the prior
  # SHA's in-flight runs, and a re-dispatched workflow cancels its own earlier
  # run on the same head via concurrency, so a cancelled bucket is a superseded
  # run whose replacement drives the state (#182). Supersession is per check
  # NAME on head: a cancel is settled only by a live run of the same name, never
  # by other checks passing. An unreplaced cancel reads `pending`, so a
  # cancelled required job that never re-ran can never read success
  # (rules/ship-on-green.md Red). A real `fail` takes precedence. A cancel whose
  # replacement never appears is bounded by the watcher's own budget
  # (watch-pr-reviews.sh pending_at_budget), never an agent-chosen timeout.
  # `skipping` stays success-equivalent (a skipped required check is a pass).
  local ci_status
  ci_status=$(echo "$checks_json" | jq -r '
    (map(select(.bucket != "cancel"))) as $live
    | (map(select(.bucket == "cancel") | .name) - ($live | map(.name))) as $unreplaced
    | if (. | length) == 0 then "none"
      elif ($live | any(.bucket == "fail")) then "failure"
      elif ($live | any(.bucket == "pending")) then "pending"
      elif ($unreplaced | length) > 0 then "pending"
      else "success" end
  ')

  local merge_state
  merge_state=$(fetch_merge_state "$owner" "$repo" "$pr_number") \
    || { echo "error: failed to fetch merge state for ${owner}/${repo}#${pr_number} — run 'gh auth status --hostname github.com' to verify auth, then retry 'gh pr view ${pr_number} --repo github.com/${owner}/${repo} --json mergeStateStatus,mergeable,headRefOid' to inspect the failing call directly" >&2; exit 1; }

  # Head SHA binds every review verdict to the commit it reviewed. Refuse to
  # proceed without it: an empty head would mark every real review stale and
  # silently void the review gate — the opposite of the fail-safe intent.
  local head_sha
  head_sha=$(printf '%s' "$merge_state" | jq -r '.head_sha // empty')
  if [[ -z "$head_sha" ]]; then
    echo "error: 'gh pr view ${pr_number} --repo github.com/${owner}/${repo}' returned no headRefOid — cannot bind review verdicts to the PR head; verify the PR number and 'gh auth status --hostname github.com', then retry" >&2
    exit 1
  fi

  local requested_logins codex_requested
  requested_logins=$(fetch_requested_logins "$owner" "$repo" "$pr_number") \
    || { echo "error: failed to fetch pending review requests for ${owner}/${repo}#${pr_number} — run 'gh auth status --hostname github.com' to verify auth, then inspect the PR's reviewRequests with 'gh api --hostname github.com graphql' (the REST requested_reviewers endpoint omits bot reviewers)" >&2; exit 1; }
  codex_requested=$(requested_among "$requested_logins" "${CODEX_REVIEW_LOGINS[@]}") \
    || { echo "error: could not match the policy reviewer against the pending review requests on ${owner}/${repo}#${pr_number} — inspect the list with 'gh api --hostname github.com graphql' for that PR's reviewRequests, then re-run this snapshot once it returns an array of reviewer logins" >&2; exit 1; }

  local codex_review claude_review codex_comments lanes_cfg
  codex_review=$(latest_review_in_lane   "$owner" "$repo" "$pr_number" "codex-policy") \
    || { echo "error: failed to fetch Codex review state" >&2; exit 1; }
  claude_review=$(latest_review_in_lane  "$owner" "$repo" "$pr_number" "claude-quality") \
    || { echo "error: failed to fetch Claude review state" >&2; exit 1; }
  lanes_cfg=$(review_lanes_config) \
    || { echo "error: failed to build the review-lane config" >&2; exit 1; }
  # Resolve each verdict against head — stale reviews collapse to "none".
  codex_review=$(resolve_review_against_head   "$codex_review"   "$head_sha")
  claude_review=$(resolve_review_against_head  "$claude_review"  "$head_sha")
  codex_review=$(printf '%s' "$codex_review" | jq --argjson r "$codex_requested" '. + {requested: $r}')
  codex_comments=$(toplevel_comments_by "$owner" "$repo" "$pr_number" "${CODEX_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count Codex inline comments" >&2; exit 1; }
  local other_reviews other_comments
  other_reviews=$(unlaned_reviews "$owner" "$repo" "$pr_number" "$head_sha") \
    || { echo "error: failed to fetch reviews outside the review lanes" >&2; exit 1; }
  other_comments=$(toplevel_comments_excluding "$owner" "$repo" "$pr_number" "${CODEX_COMMENT_LOGINS[@]}") \
    || { echo "error: failed to count inline comments from other reviewers" >&2; exit 1; }

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg head_sha "$head_sha" \
    --arg ci_status "$ci_status" \
    --argjson checks "$checks_json" \
    --argjson codex "$codex_review" \
    --argjson codex_comments "$codex_comments" \
    --argjson merge_state "$merge_state" \
    --argjson claude "$claude_review" \
    --argjson cfg "$lanes_cfg" \
    --argjson other_reviews "$other_reviews" \
    --argjson other_comments "$other_comments" \
    '{
      pr_number: $pr_number,
      head_sha: $head_sha,
      ci: {status: $ci_status, checks: $checks},
      reviews: {codex: $codex},
      lanes: {
        "codex-policy":   ($codex   + {gating: $cfg["codex-policy"].gating, expected: true}),
        "claude-quality": ($claude  + {gating: $cfg["claude-quality"].gating,
                                       expected: ($cfg["claude-quality"].workflow as $wf
                                         | $wf != null and any($checks[]; .workflow == $wf and (.bucket | IN("pass", "fail", "pending"))))})
      },
      other_reviews: $other_reviews,
      inline_comments: {codex: $codex_comments, other: $other_comments},
      merge_state: ($merge_state | {status, mergeable})
    }'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
