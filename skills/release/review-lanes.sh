#!/usr/bin/env bash
# Review-lane classifier shared by poll-pr-reviews.sh, watch-pr-reviews.sh
# (through the poll snapshot) and dismiss-stale-reviews.sh.
#
# Several reviewers can post under ONE login: the Codex policy reviewer and a
# future Claude quality reviewer both submit with the workflow GITHUB_TOKEN,
# so both author their reviews as `github-actions[bot]`. Reading "the latest
# review by that login" would then let a clean Claude comment stand in for the
# Codex verdict, and dismiss a blocking Codex CHANGES_REQUESTED on its strength
# (docs/adr/0010-review-lanes.md). Each review is therefore classified into a
# lane by its login PLUS a lane marker at the very start of its body; gating,
# "latest verdict" and "same reviewer all-clear" are all read per lane.
#
# Classification of one review object (GitHub REST shape: .user.login, .body),
# first match wins:
#   1. login in COPILOT_LOGINS                                   -> copilot
#   2. body starts with CLAUDE_QUALITY_MARKER, login in
#      CLAUDE_QUALITY_LOGINS                                     -> claude-quality
#   3. body starts with CODEX_POLICY_MARKER, login in
#      CODEX_POLICY_LOGINS                                       -> codex-policy
#   4. login in CODEX_POLICY_LOGINS (legacy, unmarked)           -> codex-policy
#   5. anything else                                             -> null (no lane)
# Rule 4 keeps every Codex review posted before the marker existed in the
# Codex lane. A marker must be the body's first bytes: a marker quoted inside
# a finding never reclassifies a review.
#
# Gating: a lane gates the merge when its *_GATES constant is true. The Claude
# lane starts advisory (ADR 0010 trial); flipping CLAUDE_QUALITY_GATES is the
# promotion. Copilot is always advisory (rules/review-severity.md).
#
# Usage (sourced): defines the constants plus
#   review_lanes_config  -> prints the lane config JSON (below) on stdout
#   review_lanes_jq      -> prints jq defs: `review_lane($cfg)` returns the
#                           lane name or null for the input review object
# Usage (executed): review-lanes.sh
# Out:   the lane config JSON on stdout:
#   {"codex-policy":   {"marker": "...", "logins": [...], "gating": true},
#    "claude-quality": {"marker": "...", "logins": [...], "gating": false},
#    "copilot":        {"marker": null,  "logins": [...], "gating": false}}
# Exit:  0 on success; 2 with a stderr diagnostic when jq is missing.

set -euo pipefail

CODEX_POLICY_MARKER="<!-- coding-policy:codex-policy-review:v1 -->"
CLAUDE_QUALITY_MARKER="<!-- coding-policy:claude-quality-review:v1 -->"

# Login sets stay arrays iterated with quoted expansion: the `[bot]` suffix is
# a glob bracket.
CODEX_POLICY_LOGINS=("github-actions[bot]")
CLAUDE_QUALITY_LOGINS=("github-actions[bot]")
COPILOT_LOGINS=("copilot-pull-request-reviewer[bot]")

CODEX_POLICY_GATES=true
CLAUDE_QUALITY_GATES=false
COPILOT_GATES=false

review_lanes_config() {
  local codex claude copilot
  codex=$(jq -n '$ARGS.positional' --args "${CODEX_POLICY_LOGINS[@]}") || return 1
  claude=$(jq -n '$ARGS.positional' --args "${CLAUDE_QUALITY_LOGINS[@]}") || return 1
  copilot=$(jq -n '$ARGS.positional' --args "${COPILOT_LOGINS[@]}") || return 1
  jq -cn \
    --arg codex_marker "$CODEX_POLICY_MARKER" \
    --arg claude_marker "$CLAUDE_QUALITY_MARKER" \
    --argjson codex "$codex" --argjson claude "$claude" --argjson copilot "$copilot" \
    --argjson codex_gates "$CODEX_POLICY_GATES" \
    --argjson claude_gates "$CLAUDE_QUALITY_GATES" \
    --argjson copilot_gates "$COPILOT_GATES" \
    '{"codex-policy":   {marker: $codex_marker,  logins: $codex,   gating: $codex_gates},
      "claude-quality": {marker: $claude_marker, logins: $claude,  gating: $claude_gates},
      "copilot":        {marker: null,           logins: $copilot, gating: $copilot_gates}}'
}

review_lanes_jq() {
  cat <<'JQ'
def review_lane($cfg):
  ((.user.login // "") | tostring) as $login
  | ((.body // "") | tostring) as $body
  | def has_login($lane): $login | IN($cfg[$lane].logins[]);
    def marked($lane): $body | startswith($cfg[$lane].marker);
    if has_login("copilot") then "copilot"
    elif has_login("claude-quality") and marked("claude-quality") then "claude-quality"
    elif has_login("codex-policy") and marked("codex-policy") then "codex-policy"
    elif has_login("codex-policy") then "codex-policy"
    else null end;
JQ
}

main() {
  if ! command -v jq >/dev/null 2>&1; then
    echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2
    return 2
  fi
  review_lanes_config | jq .
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
