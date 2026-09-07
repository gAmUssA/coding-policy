#!/usr/bin/env bash
# Resolve the publish-workflow run that corresponds to a given merge
# commit, polling until the run is enqueued and listed.
#
# After `gh pr merge`, the publish workflow may take several seconds
# to be enqueued and visible to `gh run list`. A single immediate
# lookup can return empty, which would then error `gh run watch <id>`
# downstream. This script polls the listing on a 2s interval up to a
# 30s budget and emits the matching run's database ID once it
# appears. Filters on `headSha == <merge-sha>` AND `event == push` so
# manual `workflow_dispatch` runs sharing the SHA are excluded — same
# filter the interactive Step 7 lookup uses. Selection happens inside
# `--jq` (not via `| head -n 1`) so the pipeline isn't subject to
# SIGPIPE-on-`head`-exit turning a successful match into a `pipefail`
# failure. Passes an explicit `--limit 100` so a busy main branch
# with many recent runs doesn't push the match off `gh run list`'s
# default 20-row page.
#
# Usage: resolve-publish-run.sh <owner> <repo> <merge-sha> <workflow-name>
# Out:   one JSON object on stdout: {"database_id": N} per
#        rules/script-delegation.md "JSON-producing"
# Exit:  0 on success; non-zero with stderr diagnostic if the run never
#        appears within the budget or any `gh` call fails

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
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <owner> <repo> <merge-sha> <workflow-name>" >&2
    exit 2
  fi
  validate_positive_int "RESOLVE_PUBLISH_RUN_INTERVAL_SEC" "$INTERVAL_SEC"
  validate_positive_int "RESOLVE_PUBLISH_RUN_BUDGET_SEC" "$BUDGET_SEC"
  validate_positive_int "RESOLVE_PUBLISH_RUN_LIST_LIMIT" "$RUN_LIST_LIMIT"
  if (( INTERVAL_SEC > BUDGET_SEC )); then
    echo "error: RESOLVE_PUBLISH_RUN_INTERVAL_SEC (${INTERVAL_SEC}) cannot exceed RESOLVE_PUBLISH_RUN_BUDGET_SEC (${BUDGET_SEC})" >&2
    exit 2
  fi

  local owner="$1" repo="$2" merge_sha="$3" workflow="$4"
  local elapsed=0 run_id

  # Loop bound is `<= BUDGET_SEC` (not strictly less) so a run that
  # becomes visible exactly at the budget boundary is still caught.
  # A strict `<` bound would skip the final poll at t == BUDGET_SEC.
  while (( elapsed <= BUDGET_SEC )); do
    # --jq wraps the filter in `[...] | .[0] // empty` so the output is
    # a single scalar (or empty when no match). No `| head -n 1` pipe —
    # gh's full output is fully consumed by gh's internal jq, so there's
    # no SIGPIPE window that `pipefail` could mis-attribute as failure.
    # gh's `--jq` is internal to gh; this script does NOT depend on the
    # system `jq` binary being installed.
    run_id=$(gh run list \
      --repo "${owner}/${repo}" \
      --branch main \
      --workflow "$workflow" \
      --limit "$RUN_LIST_LIMIT" \
      --json databaseId,headSha,event \
      --jq '[.[] | select(.headSha == "'"$merge_sha"'") | select(.event == "push") | .databaseId] | .[0] // empty')
    if [[ -n "$run_id" ]]; then
      if ! [[ "$run_id" =~ ^[0-9]+$ ]]; then
        echo "error: expected numeric run id from gh, got: '${run_id}' — inspect 'gh run list ... --json databaseId,headSha,event' to diagnose" >&2
        exit 1
      fi
      printf '{"database_id": %s}\n' "$run_id"
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

  echo "error: no '${workflow}' push-event run found for merge SHA ${merge_sha} on ${owner}/${repo} after ${BUDGET_SEC}s — inspect 'gh run list --repo ${owner}/${repo} --branch main --workflow \"${workflow}\" --limit ${RUN_LIST_LIMIT}' to diagnose (workflow may have failed to enqueue, or the SHA may not have triggered it)" >&2
  exit 1
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
