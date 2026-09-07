#!/usr/bin/env bash
# Request a Copilot review on a PR via GraphQL — REST silently drops bot
# reviewers. Falls back to looking up the bot ID from recent reviews if the
# pinned ID is stale, then verifies Copilot is in the mutation's OWN returned
# review requests. The REST `requested_reviewers` field omits bot reviewers, so
# it cannot verify a bot request (issue #276) — the mutation response is the
# authoritative surface.
#
# The mutation runs in UNION mode. Replace mode (the default) drops a bot-only
# request the same way the REST endpoint does — it returns success and the bot
# never lands in `reviewRequests` — and it would clear reviewers already
# requested on the PR (issue #297).
#
# Usage: request-copilot-review.sh <owner> <repo> <pr-number>
# Env:   COPILOT_BOT_ID (override default BOT_kgDOCnlnWA)
# Out:   one JSON object on stdout: {"pr_number","bot_id","requested_reviewers"}
# Exit:  0 on verified request; non-zero with stderr diagnostic on failure

set -euo pipefail

COPILOT_BOT_ID_DEFAULT="BOT_kgDOCnlnWA"

fetch_pr_node_id() {
  local owner="$1" repo="$2" pr_number="$3"
  # Validate pr_number is numeric BEFORE building the query: a
  # non-numeric value would either break the GraphQL `Int!` argument
  # or, in a more pathological case, get interpreted as additional
  # query syntax. Refuse early with a clear diagnostic.
  if [[ ! "$pr_number" =~ ^[0-9]+$ ]]; then
    echo "error: pr-number must be a positive integer; got '${pr_number}'" >&2
    return 1
  fi
  local pr_id
  # `// empty` collapses null to nothing, so a missing/invalid PR
  # produces an empty string rather than the literal "null" that --jq
  # would otherwise emit. Without this guard the downstream mutation
  # runs with `pullRequestId: "null"` and surfaces as a confusing
  # GraphQL error several steps removed from the actual root cause.
  pr_id=$(gh api graphql -f query="
    query { repository(owner: \"${owner}\", name: \"${repo}\") {
      pullRequest(number: ${pr_number}) { id }
    } }
  " --jq '.data.repository.pullRequest.id // empty')
  if [[ -z "$pr_id" ]]; then
    # Empty pr_id can come from any of: missing repository, missing
    # PR within an existing repository, insufficient permissions, or
    # a GraphQL error that still returned HTTP 200 with a partial
    # body. The diagnostic stays generic so the operator knows to
    # check all four; pinpointing the exact cause would require
    # parsing the GraphQL `errors` array, which is out of scope here.
    echo "error: failed to resolve PR node ID for PR #${pr_number} in ${owner}/${repo} (repository, permissions, GraphQL, or PR lookup may have failed)" >&2
    return 1
  fi
  echo "$pr_id"
}

# Run the requestReviews mutation and echo the resulting bot-reviewer logins as
# a JSON array. The mutation's OWN response is the authoritative post-state and
# the only surface that reports bot reviewers — the REST pulls endpoint's
# `requested_reviewers` omits them (issue #276), so it cannot verify this. On a
# GraphQL error (a stale/rejected bot ID) `gh api graphql` exits non-zero and
# stdout is empty, so a caller can branch on the exit to fall back.
#
# `union: true` is load-bearing. Without it the mutation runs in replace mode,
# which reports success and lands nothing for a bot-only request:
# `reviewRequests` comes back empty and the script exits 1 on its own
# verification, on every invocation and against a perfectly valid bot ID
# (issue #297). Replace mode would also clear any reviewer already requested
# on the PR.
#
# $3 is an optional file to capture the mutation's stderr into. Omitted on the
# first attempt, where a non-zero exit is the expected "the pinned ID may be
# stale" signal the caller handles by rediscovering; passed on the retry, where
# the failure is terminal and the GraphQL error text is the only thing that
# separates a rejected bot ID from an auth or network fault. Discarding it on
# both paths is what made the first failure undiagnosable (rules/error-handling.md
# — silencing a diagnostic while explicitly handling the failure is not
# suppression, but the terminal path must say what broke).
request_with_bot_id() {
  local pr_id="$1" bot_id="$2" err_path="${3:-/dev/null}"
  gh api graphql -f query="
    mutation { requestReviews(input: {
      pullRequestId: \"${pr_id}\", botIds: [\"${bot_id}\"], union: true
    }) { pullRequest { reviewRequests(first: 20) { nodes {
      requestedReviewer { __typename ... on Bot { login } }
    } } } } }
  " --jq '[.data.requestReviews.pullRequest.reviewRequests.nodes[]?.requestedReviewer.login // empty]' 2>"$err_path"
}

discover_copilot_bot_id() {
  # The Bot type's `login` is reported with the `[bot]` suffix in some
  # GraphQL contexts and without it in others (the REST surface keeps
  # the suffix; GraphQL is inconsistent). Match either form so the
  # filter does not silently miss a real Copilot review and run the
  # mutation against an empty/wrong actor ID.
  gh api graphql -f query="
    query { repository(owner: \"$1\", name: \"$2\") {
      pullRequests(last: 20) { nodes { reviews(first: 10) {
        nodes { author { ... on Bot { id login } } }
      } } }
    } }
  " --jq '[.data.repository.pullRequests.nodes[].reviews.nodes[]
           | select(.author.login == "copilot-pull-request-reviewer"
                    or .author.login == "copilot-pull-request-reviewer[bot]")
           | .author.id] | unique | .[0] // empty'
}

# `mutation_err` is script-global, never a main() local: the EXIT trap fires
# after main returns, when a main-local would be out of scope and this handler
# would silently skip cleanup.
mutation_err=""

# EXIT-trap cleanup. `return 0` is load-bearing — the trap's final command
# status becomes the script's exit status, so a failing `rm` would rewrite the
# verdict (rules/error-handling.md Shell Error Handling). `if ! rm` rather than
# a bare `rm`: under `set -e` a failing rm would abort the handler before
# `return 0` runs, reintroducing the rewrite the handler exists to prevent.
cleanup_mutation_err() {
  if [[ -n "${mutation_err:-}" ]]; then
    if ! rm -f "$mutation_err"; then
      echo "request-copilot-review.sh: warning: could not remove temp file ${mutation_err} — remove it by hand" >&2
    fi
  fi
  return 0
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr_number="$3"

  local pr_node_id
  pr_node_id=$(fetch_pr_node_id "$owner" "$repo" "$pr_number") || {
    echo "error: failed to fetch PR node ID for ${owner}/${repo}#${pr_number}" >&2
    exit 1
  }

  local bot_id="${COPILOT_BOT_ID:-$COPILOT_BOT_ID_DEFAULT}"
  local reviewers
  # The mutation returns the post-request reviewer list; capture it as both the
  # request AND the verification (issue #276). A non-zero exit means the pinned
  # ID was rejected — discover the live one from review history and retry.
  if ! reviewers=$(request_with_bot_id "$pr_node_id" "$bot_id"); then
    echo "warn: pinned bot ID $bot_id rejected; discovering from review history" >&2
    bot_id=$(discover_copilot_bot_id "$owner" "$repo") || {
      echo "error: failed to query review history" >&2; exit 1;
    }
    if [[ -z "$bot_id" ]]; then
      echo "error: no Copilot bot ID found in recent reviews of ${owner}/${repo}" >&2
      exit 1
    fi
    mutation_err=$(mktemp) || {
      echo "error: mktemp failed — cannot capture the mutation's error output; verify TMPDIR is set to a writable directory and re-run" >&2
      exit 2
    }
    trap cleanup_mutation_err EXIT
    reviewers=$(request_with_bot_id "$pr_node_id" "$bot_id" "$mutation_err") || {
      echo "error: requestReviews failed with discovered bot ID ${bot_id}: $(cat "$mutation_err") — the GraphQL error above names the cause; a rejected botId means Copilot is not enabled for ${owner}/${repo}, anything else is an auth or network fault ('gh auth status')" >&2
      exit 1
    }
  fi

  if ! echo "$reviewers" | jq -e 'any(test("copilot"; "i"))' >/dev/null 2>&1; then
    echo "error: Copilot not in review requests after request: $reviewers" >&2
    exit 1
  fi

  jq -n \
    --argjson pr_number "$pr_number" \
    --arg bot_id "$bot_id" \
    --argjson reviewers "$reviewers" \
    '{pr_number: $pr_number, bot_id: $bot_id, requested_reviewers: $reviewers}'
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
