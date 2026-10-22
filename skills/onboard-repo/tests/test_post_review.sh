#!/usr/bin/env bash
# Tests for post-review.sh — the mapping from Codex's structured result to the
# GitHub review payload (event + body). `gh` is mocked to capture the payload,
# so no network. Deterministic, hermetic (rules/testing-standards.md).
#
# Run: bash .github/codex-review/tests/test_post_review.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "$0")/../templates" && pwd)/post-review.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: post-review.sh not readable at $SCRIPT" >&2; exit 2; }

# shellcheck source=/dev/null
source "$SCRIPT"
set +e  # relax errexit in the harness; each main() runs under its own set -e

pass=0; fail=0
ok()  { printf 'ok   - %s\n' "$1"; pass=$((pass+1)); }
bad() { printf 'FAIL - %s\n' "$1"; fail=$((fail+1)); }
# fatal <what>: a setup step failed — stop the suite, never count it.
fatal() { echo "fatal: $1 — check that \$TMPDIR is writable, then rerun" >&2; exit 2; }

# Mock gh: capture the /reviews POST payload (arrives on stdin via --input -).
# With MOCK_422_ON_APPROVE=1, an APPROVE submit fails HTTP 422 (as github-actions[bot]
# would), so the fallback-to-COMMENT path can be exercised.
GH_CAPTURE=""
MOCK_422_ON_APPROVE=0
gh() {
  if [[ "${1:-}" == "api" && "$*" == */reviews* ]]; then
    # Pinned to github.com, so GH_HOST can never redirect the review post.
    [[ "${2:-} ${3:-}" == "--hostname github.com" ]] || { echo "mock gh: review post not pinned to github.com: $*" >&2; return 97; }
    local payload; payload=$(cat)
    printf '%s' "$payload" > "$GH_CAPTURE" || { echo "mock gh: cannot capture the payload to $GH_CAPTURE" >&2; return 98; }
    local ev; ev=$(jq -r '.event' <<<"$payload")
    if [[ "$MOCK_422_ON_APPROVE" == "1" && "$ev" == "APPROVE" ]]; then
      echo "gh: Unprocessable Entity (HTTP 422)" >&2
      return 1
    fi
    return 0
  fi
  return 0
}

run_main() { ( set -euo pipefail; main "$@" ); }

# --- pass, no findings -> APPROVE (token can approve), body is the summary ---
t_pass() {
  local dir; dir=$(mktemp -d) || { bad "pass: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  printf '{"summary":"Policy loaded: 23 rule files from gamussa/coding-policy. All rules pass.","findings":[]}' > "$dir/final.json" || fatal "cannot write $dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                              || { bad "pass: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "APPROVE" ]]             || { bad "pass: event APPROVE (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "0" ]]                 || { bad "pass: findings 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "APPROVE" ]]         || { bad "pass: payload event APPROVE"; rm -rf "$dir"; return; }
  # Assert the body begins with the load indicator (schema contract), not merely
  # that some line within it does — `grep -E "^..."` anchors per line, so a
  # preamble line before the summary would pass it. Here `=~` matches against the
  # whole body string (not line by line), so the pattern's leading `^` anchors to
  # the body's start.
  # The lane marker line comes first (the classifier needs it at byte 0), then
  # the summary.
  local body indicator_re=$'^<!-- coding-policy:codex-policy-review:v1 -->\nPolicy loaded: [0-9]+ rule files'
  body=$(jq -r .body "$GH_CAPTURE")
  [[ "$body" =~ $indicator_re ]]                              || { bad "pass: body begins with the lane marker, then the load indicator"; rm -rf "$dir"; return; }
  ok "no findings -> APPROVE, marker + summary body, 0 findings"
  rm -rf "$dir"
}

# --- pass but token can't approve (HTTP 422) -> falls back to COMMENT ---
t_pass_422_fallback() {
  local dir; dir=$(mktemp -d) || { bad "fallback: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=1
  printf '{"summary":"Policy loaded: 23 rule files from gamussa/coding-policy. Clean.","findings":[]}' > "$dir/final.json" || fatal "cannot write $dir/final.json"
  local out; out=$(run_main owner repo 5 "$dir/final.json" 2>/dev/null)
  local rc=$?
  MOCK_422_ON_APPROVE=0
  [[ $rc -eq 0 ]]                                              || { bad "fallback: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "COMMENT" ]]             || { bad "fallback: output event COMMENT (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "COMMENT" ]]         || { bad "fallback: last payload event COMMENT"; rm -rf "$dir"; return; }
  ok "pass + APPROVE 422 -> falls back to COMMENT"
  rm -rf "$dir"
}

# --- a blocking finding -> REQUEST_CHANGES, findings in a Blocking section ---
t_blocking() {
  local dir; dir=$(mktemp -d) || fatal "mktemp -d failed"
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON' || fatal "cannot write $dir/final.json"
{"summary":"Policy loaded: 23 rule files from rules/. One violation.",
 "findings":[{"path":"skills/x/run.sh","line":3,"rule":"error-handling","severity":"blocking","message":"missing set -euo pipefail; add it at the top"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "blocking: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "blocking: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "1" ]]                       || { bad "blocking: findings 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "blocking: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"        || { bad "blocking: body has a Blocking section"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "skills/x/run.sh:3"           || { bad "blocking: body cites the finding path:line"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "error-handling"             || { bad "blocking: body names the rule"; rm -rf "$dir"; return; }
  ok "blocking finding -> REQUEST_CHANGES, Blocking section in body"
  rm -rf "$dir"
}

# --- advisory-only findings -> COMMENT (never gates), Advisory section ---
t_advisory_only() {
  local dir; dir=$(mktemp -d) || fatal "mktemp -d failed"
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  cat > "$dir/final.json" <<'JSON' || fatal "cannot write $dir/final.json"
{"summary":"Policy loaded: 23 rule files from rules/. Style nit only.",
 "findings":[{"path":"rules/foo.md","line":8,"rule":"context-artifacts","severity":"advisory","message":"em-dash clause attaches a rationale; drop it"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "advisory: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "COMMENT" ]]                    || { bad "advisory: event COMMENT (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .findings <<<"$out")" == "1" ]]                       || { bad "advisory: findings 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "0" ]]                       || { bad "advisory: blocking 0 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .advisory <<<"$out")" == "1" ]]                       || { bad "advisory: advisory 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event "$GH_CAPTURE")" == "COMMENT" ]]               || { bad "advisory: payload event COMMENT"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Advisory findings"        || { bad "advisory: body has an Advisory section"; rm -rf "$dir"; return; }
  if jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"; then bad "advisory: body must not have a Blocking section"; rm -rf "$dir"; return; fi
  ok "advisory-only -> COMMENT (never gates), Advisory section"
  rm -rf "$dir"
}

# --- mixed blocking + advisory -> REQUEST_CHANGES, both sections present ---
t_mixed() {
  local dir; dir=$(mktemp -d) || fatal "mktemp -d failed"
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON' || fatal "cannot write $dir/final.json"
{"summary":"Policy loaded: 23 rule files from rules/. Mixed.",
 "findings":[
   {"path":"a.sh","line":1,"rule":"no-secrets","severity":"blocking","message":"hardcoded token; move to env"},
   {"path":"rules/b.md","line":2,"rule":"context-artifacts","severity":"advisory","message":"prefer a synonym"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "mixed: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "mixed: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "mixed: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .advisory <<<"$out")" == "1" ]]                       || { bad "mixed: advisory 1 (got $out)"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Blocking findings"        || { bad "mixed: body has a Blocking section"; rm -rf "$dir"; return; }
  jq -r .body "$GH_CAPTURE" | grep -q "## Advisory findings"        || { bad "mixed: body has an Advisory section"; rm -rf "$dir"; return; }
  ok "mixed blocking+advisory -> REQUEST_CHANGES, both sections"
  rm -rf "$dir"
}

# --- missing severity is treated as blocking (fail-safe) ---
t_missing_severity_blocks() {
  local dir; dir=$(mktemp -d) || fatal "mktemp -d failed"
  GH_CAPTURE="$dir/payload.json"
  cat > "$dir/final.json" <<'JSON' || fatal "cannot write $dir/final.json"
{"summary":"Policy loaded: 23 rule files from rules/. Unclassified finding.",
 "findings":[{"path":"x.sh","line":1,"rule":"error-handling","message":"no severity field"}]}
JSON
  local out; out=$(run_main owner repo 9 "$dir/final.json")
  local rc=$?
  [[ $rc -eq 0 ]]                                                     || { bad "missing-severity: exit 0 (rc=$rc)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .event <<<"$out")" == "REQUEST_CHANGES" ]]            || { bad "missing-severity: event REQUEST_CHANGES (got $out)"; rm -rf "$dir"; return; }
  [[ "$(jq -r .blocking <<<"$out")" == "1" ]]                       || { bad "missing-severity: blocking 1 (got $out)"; rm -rf "$dir"; return; }
  ok "missing severity -> treated as blocking (fail-safe)"
  rm -rf "$dir"
}

# --- missing result file -> exit 1 ---
t_missing_file() {
  local rc=0; run_main owner repo 1 "/nonexistent/final.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "missing result file -> exit 1"; else bad "missing result file -> exit 1 (rc=$rc)"; fi
}

# --- invalid JSON -> exit 1 ---
t_invalid_json() {
  local dir; dir=$(mktemp -d) || { bad "invalid_json: mktemp -d failed"; return; }
  printf 'not json at all' > "$dir/final.json" || fatal "cannot write $dir/final.json"
  local rc=0; run_main owner repo 1 "$dir/final.json" >/dev/null 2>&1 || rc=$?
  if [[ $rc -eq 1 ]]; then ok "invalid JSON -> exit 1"; else bad "invalid JSON -> exit 1 (rc=$rc)"; fi
  rm -rf "$dir"
}

# The posted body must classify into the codex-policy lane BY MARKER through
# the release skill's classifier entry point (skills/release/review-lanes.sh),
# so a reviewer sharing the login can be told apart (ADR 0010).
t_body_classifies_by_marker() {
  local dir; dir=$(mktemp -d) || { bad "marker: mktemp -d failed"; return; }
  GH_CAPTURE="$dir/payload.json"; MOCK_422_ON_APPROVE=0
  printf '{"summary":"Policy loaded: 23 rule files from rules/. One nit.","findings":[{"path":"a.sh","line":1,"rule":"file-hygiene","message":"m","severity":"advisory"}]}' > "$dir/final.json" || fatal "cannot write $dir/final.json"
  run_main owner repo 5 "$dir/final.json" >/dev/null || { bad "marker: post-review.sh failed"; rm -rf "$dir"; return; }
  local lanes cfg
  lanes="$(cd "$(dirname "$SCRIPT")/../../release" && pwd)/review-lanes.sh"
  cfg=$(bash "$lanes") || { bad "marker: review-lanes.sh failed"; rm -rf "$dir"; return; }
  if jq -e --argjson cfg "$cfg" '.body | startswith($cfg["codex-policy"].marker)' "$GH_CAPTURE" >/dev/null; then
    ok "posted body carries the codex-policy lane marker the classifier reads"
  else
    bad "posted body does not start with the classifier's codex-policy marker"
  fi
  rm -rf "$dir"
}

echo "== post-review.sh tests =="
t_pass
t_pass_422_fallback
t_blocking
t_advisory_only
t_mixed
t_missing_severity_blocks
t_missing_file
t_invalid_json
t_body_classifies_by_marker
echo "== summary: ${pass} passed, ${fail} failed =="
echo "test-summary: passed=${pass} failed=${fail} token=${SUMMARY_TOKEN}"
[[ "$fail" -eq 0 ]]
