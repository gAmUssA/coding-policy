#!/usr/bin/env bash
# Block until a PR reaches a merge-gate-relevant terminal state, polling
# poll-pr-reviews.sh at a script-owned interval up to a script-owned budget.
#
# poll-pr-reviews.sh is a single non-blocking snapshot; the loop that wraps
# it — how often to poll, how long to wait, what state ends it — used to be
# hand-rolled per agent, which is how invented wall-clock timeouts (and
# fabricated "N minutes in the policy" citations) crept in. This script owns
# that loop so the cadence and give-up budget are one tuned constant, not an
# agent choice, and it watches EXACTLY the fields the Step 7 merge gate reads:
# both bots' LATEST review state (resolved by bot login inside
# poll-pr-reviews.sh), CI status, and merge state. Only the policy reviewer
# gates (rules/review-severity.md — Copilot is always advisory); it waits for
# Copilot to post so its body can be read, never gates on its verdict. It does
# NOT wait for inline comments to appear (a verdict with zero inline comments is
# a complete review) and does NOT key off any hand-picked run/comment/check id.
#
# Usage: watch-pr-reviews.sh <owner> <repo> <pr-number>
# Out:   the full poll-pr-reviews.sh snapshot with an added top-level object:
#          "watch": {"result": "<result>", "attempts": N, "elapsed_seconds": N}
#        emitted on stdout when a terminal state is reached (rc 0 or 1).
# Exit / result matrix (branch on .watch.result):
#   rc 0, result "ready"             — mergeable, CI success/none, both bots
#                                      posted (so their bodies can be read), and
#                                      the policy reviewer is not
#                                      CHANGES_REQUESTED. Copilot may be
#                                      CHANGES_REQUESTED and this still reaches
#                                      ready (Copilot is always advisory).
#   rc 0, result "changes_requested" — the policy reviewer's latest verdict is
#                                      CHANGES_REQUESTED (a blocking finding is
#                                      present — advisory-only reviews post
#                                      COMMENT, see rules/review-severity.md).
#                                      Copilot is always advisory and never
#                                      produces this result. Address and re-push.
#   rc 0, result "ci_failure"        — a check failed. Fix and re-push.
#   rc 0, result "dirty"             — branch conflicts with the base; GitHub
#                                      skipped the pull_request: workflows.
#                                      Rebase, resolve, force-push, re-run.
#   rc 1, result "pending_at_budget" — a signal never arrived within the
#                                      budget (a reviewer that never posted,
#                                      CI stuck pending). Inspect which field
#                                      is still none/pending in the snapshot.
#   rc 2 — argument/env validation error, missing jq, or a poll-pr-reviews.sh
#          failure; stderr-only diagnostic, stdout empty. The watch is
#          idempotent — it re-derives state from scratch — so re-run to resume.
#
# The interval and budget are the constants below (override via the matching
# env vars, used by the test harness to keep runs fast) — never by wrapping
# this call in `timeout`. rules/ci-safety.md "Always Watch CI" names the
# contract and points here rather than restating these.

set -euo pipefail

INTERVAL_SEC="${WATCH_PR_REVIEWS_INTERVAL_SEC:-15}"
BUDGET_SEC="${WATCH_PR_REVIEWS_BUDGET_SEC:-900}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
POLL_CMD="${WATCH_PR_REVIEWS_POLL_CMD:-${SCRIPT_DIR}/poll-pr-reviews.sh}"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

validate_positive_int() {
  local name="$1" value="$2"
  if ! [[ "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "error: ${name} must be a positive integer, got: '${value}'" >&2
    exit 2
  fi
}

# Overridable in tests so the loop can be driven off queued snapshots
# without a live gh. In production this shells out to poll-pr-reviews.sh
# (invoked via `bash <path>` — tessl packaging strips the exec bit).
fetch_snapshot() {
  local owner="$1" repo="$2" pr="$3"
  bash "$POLL_CMD" "$owner" "$repo" "$pr"
}

emit_and_exit() {
  local result="$1" attempts="$2" elapsed="$3" snapshot="$4" rc="$5"
  printf '%s' "$snapshot" | jq \
    --arg result "$result" \
    --argjson attempts "$attempts" \
    --argjson elapsed "$elapsed" \
    '. + {watch: {result: $result, attempts: $attempts, elapsed_seconds: $elapsed}}'
  exit "$rc"
}

main() {
  if [[ $# -ne 3 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number>" >&2
    exit 2
  fi
  validate_positive_int "WATCH_PR_REVIEWS_INTERVAL_SEC" "$INTERVAL_SEC"
  validate_positive_int "WATCH_PR_REVIEWS_BUDGET_SEC" "$BUDGET_SEC"
  if (( INTERVAL_SEC > BUDGET_SEC )); then
    echo "error: WATCH_PR_REVIEWS_INTERVAL_SEC (${INTERVAL_SEC}) cannot exceed WATCH_PR_REVIEWS_BUDGET_SEC (${BUDGET_SEC})" >&2
    exit 2
  fi

  local owner="$1" repo="$2" pr="$3"
  local elapsed=0 attempts=0 snapshot=""

  # Loop bound is `<= BUDGET_SEC` (not strictly less) so a signal that lands
  # exactly at the budget boundary is still caught by the final poll.
  while (( elapsed <= BUDGET_SEC )); do
    attempts=$(( attempts + 1 ))

    snapshot=$(fetch_snapshot "$owner" "$repo" "$pr") \
      || { echo "error: poll-pr-reviews.sh failed for ${owner}/${repo}#${pr} (attempt ${attempts}) — run 'bash ${POLL_CMD} ${owner} ${repo} ${pr}' directly to see the underlying gh/jq error; the watch is idempotent, re-run once resolved" >&2; exit 2; }

    if ! printf '%s' "$snapshot" | jq -e . >/dev/null 2>&1; then
      echo "error: poll-pr-reviews.sh returned non-JSON for ${owner}/${repo}#${pr} (attempt ${attempts}): ${snapshot}" >&2
      exit 2
    fi

    local mergeable mstatus ci codex copilot
    mergeable=$(printf '%s' "$snapshot" | jq -r '.merge_state.mergeable')
    mstatus=$(printf '%s'   "$snapshot" | jq -r '.merge_state.status')
    ci=$(printf '%s'        "$snapshot" | jq -r '.ci.status')
    codex=$(printf '%s'     "$snapshot" | jq -r '.reviews.codex.state')
    copilot=$(printf '%s'   "$snapshot" | jq -r '.reviews.copilot.state')

    # Order matters: a conflicting branch or a failed check or a blocking
    # CHANGES_REQUESTED verdict is terminal-for-this-round — the agent must
    # go act (rebase / fix / address) before any further waiting helps.
    if [[ "$mergeable" == "CONFLICTING" || "$mstatus" == "DIRTY" ]]; then
      emit_and_exit "dirty" "$attempts" "$elapsed" "$snapshot" 0
    fi
    if [[ "$ci" == "failure" ]]; then
      emit_and_exit "ci_failure" "$attempts" "$elapsed" "$snapshot" 0
    fi
    # Only the policy reviewer gates. Its CHANGES_REQUESTED means a blocking
    # finding is present (post-review.sh derives the event from per-finding
    # severity; advisory-only reviews post COMMENT). Copilot is always advisory
    # (rules/review-severity.md) — its verdict never produces this result; the
    # agent still reads its body per rules/reviewer-feedback-reading.md.
    if [[ "$codex" == "CHANGES_REQUESTED" ]]; then
      emit_and_exit "changes_requested" "$attempts" "$elapsed" "$snapshot" 0
    fi

    # Ready = the exact Step 7 merge-gate field conjunction: mergeable, CI
    # green-or-absent, and BOTH bots have posted a verdict (state left "none")
    # so each body can be read before merge. The policy reviewer is not
    # CHANGES_REQUESTED here — the guard above returned; Copilot never gates.
    if [[ "$mergeable" == "MERGEABLE" \
       && ( "$ci" == "success" || "$ci" == "none" ) \
       && "$codex" != "none" \
       && "$copilot" != "none" ]]; then
      emit_and_exit "ready" "$attempts" "$elapsed" "$snapshot" 0
    fi

    # Still pending (UNKNOWN mergeability, CI pending, or a bot yet to post).
    # Skip the trailing sleep on the boundary iteration; otherwise cap the
    # sleep at remaining budget so total sleep tracks BUDGET_SEC.
    (( elapsed == BUDGET_SEC )) && break
    local remaining=$(( BUDGET_SEC - elapsed ))
    local sleep_for=$INTERVAL_SEC
    (( sleep_for > remaining )) && sleep_for=$remaining
    sleep "$sleep_for"
    elapsed=$(( elapsed + sleep_for ))
  done

  emit_and_exit "pending_at_budget" "$attempts" "$elapsed" "$snapshot" 1
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
