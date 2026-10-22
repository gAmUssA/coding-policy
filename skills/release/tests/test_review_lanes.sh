#!/usr/bin/env bash
# Outcome-based tests for review-lanes.sh: the executable's lane config and
# the sourced `review_lane($cfg)` classifier over real-shaped GitHub review
# objects (docs/adr/0010-review-lanes.md).
#
# Run: bash skills/release/tests/test_review_lanes.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/review-lanes.sh"
[[ -r "$SCRIPT" ]] || { echo "fatal: review-lanes.sh not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq is required to run these tests" >&2; exit 2; }

# shellcheck source=skills/release/review-lanes.sh
source "$SCRIPT" || { echo "fatal: cannot source $SCRIPT" >&2; exit 2; }
set +e

FAIL_COUNT=0
PASS_COUNT=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  [[ "$expected" == "$actual" ]] && return 0
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Classify one review object through the shipped config and jq defs.
lane_of() { # <login> <body-json-string-or-null>
  local cfg
  cfg=$(bash "$SCRIPT") || return 1
  jq -cn --arg login "$1" --argjson body "$2" \
    '{id: 1, user: {login: $login, type: "Bot"}, state: "COMMENTED", body: $body}' \
    | jq -r --argjson cfg "$cfg" "$(review_lanes_jq) review_lane(\$cfg)"
}

t_config_shape() {
  local cfg
  cfg=$(bash "$SCRIPT") || return 1
  assert_eq "codex gates"   "true"  "$(jq -r '.["codex-policy"].gating' <<<"$cfg")" || return 1
  assert_eq "claude gates"  "false" "$(jq -r '.["claude-quality"].gating' <<<"$cfg")" || return 1
  assert_eq "copilot gates" "false" "$(jq -r '.copilot.gating' <<<"$cfg")" || return 1
  assert_eq "codex marker" "<!-- coding-policy:codex-policy-review:v1 -->" "$(jq -r '.["codex-policy"].marker' <<<"$cfg")" || return 1
  assert_eq "claude marker" "<!-- coding-policy:claude-quality-review:v1 -->" "$(jq -r '.["claude-quality"].marker' <<<"$cfg")"
}

t_claude_marker_under_shared_login() {
  assert_eq "lane" "claude-quality" "$(lane_of 'github-actions[bot]' '"<!-- coding-policy:claude-quality-review:v1 -->\nNo issues."')"
}

t_codex_marker() {
  assert_eq "lane" "codex-policy" "$(lane_of 'github-actions[bot]' '"<!-- coding-policy:codex-policy-review:v1 -->\nPolicy loaded: 30 rule files."')"
}

t_unmarked_legacy_is_codex() {
  assert_eq "plain body" "codex-policy" "$(lane_of 'github-actions[bot]' '"Policy loaded: 30 rule files."')" || return 1
  assert_eq "null body"  "codex-policy" "$(lane_of 'github-actions[bot]' 'null')"
}

# A marker anywhere but the first bytes never reclassifies a review.
t_quoted_marker_does_not_reclassify() {
  assert_eq "lane" "codex-policy" "$(lane_of 'github-actions[bot]' '"Policy loaded. Finding quotes <!-- coding-policy:claude-quality-review:v1 -->"')"
}

t_copilot_login() {
  assert_eq "lane" "copilot" "$(lane_of 'copilot-pull-request-reviewer[bot]' '"<!-- coding-policy:claude-quality-review:v1 -->"')"
}

# A marker from a login no lane trusts claims no lane.
t_untrusted_login_has_no_lane() {
  assert_eq "human with claude marker" "null" "$(lane_of 'some-human' '"<!-- coding-policy:claude-quality-review:v1 -->"')" || return 1
  assert_eq "human with codex marker"  "null" "$(lane_of 'some-human' '"<!-- coding-policy:codex-policy-review:v1 -->"')"
}

echo "== review-lanes.sh tests =="
run "the executable emits the lane config"            t_config_shape
run "a Claude marker under the shared login is Claude" t_claude_marker_under_shared_login
run "a Codex marker is Codex"                          t_codex_marker
run "an unmarked legacy review is Codex by login"      t_unmarked_legacy_is_codex
run "a quoted marker does not reclassify"              t_quoted_marker_does_not_reclassify
run "the Copilot login is Copilot"                     t_copilot_login
run "an untrusted login claims no lane"                t_untrusted_login_has_no_lane

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
echo "test-summary: passed=${PASS_COUNT} failed=${FAIL_COUNT} token=${SUMMARY_TOKEN}"
[[ "$FAIL_COUNT" -eq 0 ]]
