#!/usr/bin/env bash
# Post a Codex policy-review verdict onto a pull request as a GitHub review.
#
# Reads the structured result Codex produced (the --output-last-message file,
# shaped by .github/codex-review/schema.json) and submits ONE pull-request
# review whose body carries the summary and every finding. Findings ride in
# the review body rather than as inline comments so an off-diff line can never
# trigger the HTTP 422 that would cascade-fail the whole review.
#
# The review event is DERIVED from per-finding severity, not read from a model
# verdict (rules/review-severity.md — the gate is a deterministic function of
# severities, rules/script-delegation.md):
#   - any finding with severity "blocking"  -> REQUEST_CHANGES (gates the merge)
#   - findings, all "advisory"              -> COMMENT (visible, never gates)
#   - no findings                           -> APPROVE (clean pass)
#
# The review is authored by whoever GH_TOKEN belongs to. A token GitHub forbids
# from approving (`github-actions[bot]` — HTTP 422) falls back to COMMENT on a
# clean pass, so the verdict is never lost.
#
# Usage: post-review.sh <owner> <repo> <pr-number> <result-json-file>
# Out:   one JSON object on stdout:
#          {"state":"posted","event":"...","findings":N,"blocking":N,"advisory":N}
# Exit:  0 on success; non-zero with a stderr diagnostic on failure.

set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
  exit 2
fi

# Submit ONE review event to the PR, retrying transient failures with backoff.
# Args: <owner> <repo> <pr> <event> <body>
# Return: 0 posted; 22 the event was rejected as unprocessable (HTTP 422 — e.g.
#   an APPROVE from a token that cannot approve); 1 other failure after retries.
submit_review() {
  local owner="$1" repo="$2" pr="$3" event="$4" body="$5"
  local payload attempt=0 max=3 err
  payload=$(jq -n --arg event "$event" --arg body "$body" '{event: $event, body: $body}')
  while :; do
    attempt=$((attempt + 1))
    # Capture stderr (stdout discarded) so a non-retryable 422 can be told apart
    # from any other failure (which is retried).
    if err=$(printf '%s' "$payload" | gh api "repos/${owner}/${repo}/pulls/${pr}/reviews" --method POST --input - 2>&1 1>/dev/null); then
      return 0
    fi
    # HTTP 422 (unprocessable) is not transient — do not retry; let the caller fall back.
    if grep -q "HTTP 422" <<<"$err"; then
      printf '%s\n' "$err" >&2
      return 22
    fi
    if (( attempt >= max )); then
      printf '%s\n' "$err" >&2
      echo "error: failed to submit the review on ${owner}/${repo}#${pr} after ${max} attempts — see the gh error above (token scope, PR state, or a GitHub API outage)" >&2
      return 1
    fi
    echo "post-review: submit attempt ${attempt} failed — retrying in $(( attempt * 5 ))s" >&2
    sleep $(( attempt * 5 ))
  done
}

main() {
  if [[ $# -ne 4 ]]; then
    echo "usage: $0 <owner> <repo> <pr-number> <result-json-file>" >&2
    exit 2
  fi
  local owner="$1" repo="$2" pr="$3" result="$4"

  if [[ ! -f "$result" ]]; then
    echo "error: result file not found: ${result} — codex exec did not write its --output-last-message file; check the review step logs" >&2
    exit 1
  fi
  if ! jq -e . "$result" >/dev/null 2>&1; then
    echo "error: ${result} is not valid JSON — codex exec did not honor --output-schema; inspect the file and the review step logs" >&2
    exit 1
  fi

  local summary findings_count blocking_count advisory_count
  summary=$(jq -r '.summary // ""' "$result")
  findings_count=$(jq '(.findings // []) | length' "$result")
  # A finding with no severity (older reviewer that predates the severity field,
  # or a malformed entry) is treated as blocking — fail safe: never let an
  # unclassified finding slip through as non-gating.
  blocking_count=$(jq '[(.findings // [])[] | select((.severity // "blocking") != "advisory")] | length' "$result")
  advisory_count=$(( findings_count - blocking_count ))

  # Derive the event from severity (rules/review-severity.md), never a model verdict.
  local event
  if   (( blocking_count > 0 )); then event="REQUEST_CHANGES"
  elif (( findings_count > 0 )); then event="COMMENT"
  else                                event="APPROVE"
  fi

  # Build the review body: summary, then blocking and advisory findings in
  # labeled sections (each omitted when its tier is empty).
  local body blocking_md advisory_md
  body="$summary"
  if (( blocking_count > 0 )); then
    blocking_md=$(jq -r '.findings[] | select((.severity // "blocking") != "advisory") | "- `\(.path):\(.line)` — **\(.rule)** — \(.message)"' "$result")
    body="${body}"$'\n\n'"## Blocking findings (gate the merge)"$'\n'"${blocking_md}"
  fi
  if (( advisory_count > 0 )); then
    advisory_md=$(jq -r '.findings[] | select((.severity // "blocking") == "advisory") | "- `\(.path):\(.line)` — **\(.rule)** — \(.message)"' "$result")
    body="${body}"$'\n\n'"## Advisory findings (do not gate)"$'\n'"${advisory_md}"
  fi

  # A pass is APPROVE when the token can approve (a GitHub App); a token that
  # cannot approve (github-actions[bot] — HTTP 422) falls back to COMMENT so the
  # verdict is never lost. The output reports the event actually posted.
  local posted="$event" rc=0
  submit_review "$owner" "$repo" "$pr" "$event" "$body" || rc=$?
  if (( rc == 22 )) && [[ "$event" == "APPROVE" ]]; then
    echo "post-review: APPROVE rejected as unprocessable (HTTP 422) — this token cannot approve; posting COMMENT instead" >&2
    posted="COMMENT"
    submit_review "$owner" "$repo" "$pr" "COMMENT" "$body" || exit 1
  elif (( rc != 0 )); then
    exit 1
  fi

  jq -n --arg event "$posted" \
    --argjson findings "$findings_count" \
    --argjson blocking "$blocking_count" \
    --argjson advisory "$advisory_count" \
    '{state: "posted", event: $event, findings: $findings, blocking: $blocking, advisory: $advisory}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
