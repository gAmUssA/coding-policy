#!/usr/bin/env bash
# Resolve the publish-workflow run that corresponds to a given commit on
# a given ref, polling until the run is enqueued and listed.
#
# After `gh pr merge` (or a `git push` of a release tag), the publish
# workflow may take several seconds to be enqueued and visible to
# `gh run list`. A single immediate lookup can return empty, which would
# then error `gh run watch <id>` downstream. This script polls the
# listing on a 2s interval up to a 30s budget and emits the matching
# run's database ID once it appears.
#
# Selection binds four facts, so a run that shares only some of them is
# never mistaken for this publication's run:
#   workflow  — `--workflow <name>`
#   ref       — `--branch <ref>` server-side, re-checked on `headBranch`
#               client-side. A tag push carries the tag name as its
#               `headBranch`, which is what makes an explicit tag ref
#               resolvable through the same path as a `main` push.
#   commit    — `headSha == <head-sha>`
#   event     — `event == "push"`, so a manual `workflow_dispatch`
#               sharing the commit is excluded
# Unrelated branches or tags pointing at the same commit fail the ref
# check and are excluded with them.
#
# The filter emits EVERY matching run id, not the first. Two runs
# matching all four facts is an ambiguity the caller must resolve — a
# re-pushed tag, a re-created ref — so this script refuses with a
# diagnostic naming the candidates instead of silently picking a winner.
#
# Selection happens inside `--jq` (not via `| head -n 1`) so the
# pipeline isn't subject to SIGPIPE-on-`head`-exit turning a successful
# match into a `pipefail` failure. Passes an explicit `--limit 100` so a
# busy ref with many recent runs doesn't push the match off
# `gh run list`'s default 20-row page.
#
# Usage: resolve-publish-run.sh <owner> <repo> <head-sha> <workflow-name> [ref]
#        <ref> defaults to `main` — the four-argument merge-to-main form
#        is unchanged. Pass a tag name (`v0.1.4`) to resolve a
#        tag-triggered release run.
# Out:   one JSON object on stdout: {"database_id": N} per
#        rules/script-delegation.md "JSON-producing"
# Exit:  0 on success; 1 if the run never appears within the budget, if
#        more than one run matches, or if any `gh` call fails; 2 on a
#        usage or configuration error

set -euo pipefail

INTERVAL_SEC="${RESOLVE_PUBLISH_RUN_INTERVAL_SEC:-2}"
BUDGET_SEC="${RESOLVE_PUBLISH_RUN_BUDGET_SEC:-30}"
RUN_LIST_LIMIT="${RESOLVE_PUBLISH_RUN_LIST_LIMIT:-100}"

validate_positive_int() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: ${name} must be a positive integer, got: '${value}'" >&2
    exit 2
  fi
}

main() {
  if [[ $# -lt 4 || $# -gt 5 ]]; then
    echo "usage: $0 <owner> <repo> <head-sha> <workflow-name> [ref]" >&2
    exit 2
  fi
  validate_positive_int "RESOLVE_PUBLISH_RUN_INTERVAL_SEC" "$INTERVAL_SEC"
  validate_positive_int "RESOLVE_PUBLISH_RUN_BUDGET_SEC" "$BUDGET_SEC"
  validate_positive_int "RESOLVE_PUBLISH_RUN_LIST_LIMIT" "$RUN_LIST_LIMIT"
  if (( INTERVAL_SEC > BUDGET_SEC )); then
    echo "error: RESOLVE_PUBLISH_RUN_INTERVAL_SEC (${INTERVAL_SEC}) cannot exceed RESOLVE_PUBLISH_RUN_BUDGET_SEC (${BUDGET_SEC})" >&2
    exit 2
  fi

  # `${5-main}` (not `${5:-main}`): an unset fifth argument takes the
  # `main` default, while an EMPTY one — a caller expanding an unset
  # variable — falls through to the guard below instead of silently
  # resolving main's run for a tag release.
  local owner="$1" repo="$2" head_sha="$3" workflow="$4" ref="${5-main}"
  if [[ -z "$ref" ]]; then
    echo "error: ref must not be empty — omit the argument for the default 'main', or pass a branch or tag name" >&2
    exit 2
  fi
  local elapsed=0 matches match_count

  # Git refs permit quotes. Encode both interpolated arguments as jq
  # string contents so their bytes cannot alter the selection predicate.
  # Backslashes must be escaped first, including for malformed SHA input.
  local jq_sha="${head_sha//\\/\\\\}" jq_ref="${ref//\\/\\\\}"
  jq_sha="${jq_sha//\"/\\\"}"
  jq_ref="${jq_ref//\"/\\\"}"

  # Loop bound is `<= BUDGET_SEC` (not strictly less) so a run that
  # becomes visible exactly at the budget boundary is still caught.
  # A strict `<` bound would skip the final poll at t == BUDGET_SEC.
  while (( elapsed <= BUDGET_SEC )); do
    # --jq emits one id per matching run, so the ambiguity check below
    # sees every candidate. gh's `--jq` is internal to gh; this script
    # does NOT depend on the system `jq` binary being installed.
    matches=$(gh run list \
      --repo "${owner}/${repo}" \
      --branch "$ref" \
      --workflow "$workflow" \
      --limit "$RUN_LIST_LIMIT" \
      --json databaseId,headSha,event,headBranch \
      --jq '.[] | select(.headSha == "'"$jq_sha"'") | select(.event == "push") | select(.headBranch == "'"$jq_ref"'") | .databaseId')
    if [[ -n "$matches" ]]; then
      match_count=$(printf '%s\n' "$matches" | wc -l | tr -d ' ')
      if (( match_count > 1 )); then
        echo "error: ${match_count} '${workflow}' push-event runs match commit ${head_sha} on ref ${ref} in ${owner}/${repo}: $(printf '%s' "$matches" | tr '\n' ' ')— resolve by hand with 'gh run view <id>' and watch the intended run, or delete the duplicate ref push that created the second run" >&2
        exit 1
      fi
      if ! [[ "$matches" =~ ^[0-9]+$ ]]; then
        echo "error: expected numeric run id from gh, got: '${matches}' — inspect 'gh run list ... --json databaseId,headSha,event,headBranch' to diagnose" >&2
        exit 1
      fi
      printf '{"database_id": %s}\n' "$matches"
      return 0
    fi
    # Skip the trailing sleep on the boundary iteration so the loop
    # exits immediately after polling at t == BUDGET_SEC. Otherwise
    # cap sleep at (BUDGET - elapsed) so the total sleep accounting
    # tracks BUDGET_SEC for non-divisible intervals (e.g., interval=2
    # + budget=3 → sleeps 2, 1 instead of 2, 2). Note: this bounds
    # total SLEEP time, not real wall-clock — `gh run list` API calls
    # also take time, so the loop's true wall-clock can exceed
    # BUDGET_SEC by a few seconds of network latency.
    (( elapsed == BUDGET_SEC )) && break
    local remaining=$(( BUDGET_SEC - elapsed ))
    local sleep_for=$INTERVAL_SEC
    (( sleep_for > remaining )) && sleep_for=$remaining
    sleep "$sleep_for"
    elapsed=$(( elapsed + sleep_for ))
  done

  echo "error: no '${workflow}' push-event run found for commit ${head_sha} on ref ${ref} in ${owner}/${repo} after ${BUDGET_SEC}s — inspect 'gh run list --repo ${owner}/${repo} --branch ${ref} --workflow \"${workflow}\" --limit ${RUN_LIST_LIMIT}' to diagnose (workflow may have failed to enqueue, or the commit may not have triggered it on that ref)" >&2
  exit 1
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
