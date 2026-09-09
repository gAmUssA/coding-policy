#!/usr/bin/env bash
# Outcome-based tests for verify-github-release.sh.
#
# Covers the answers the script promises:
#   1. Successful run + published release, every asset uploaded — rc 0,
#      ok true, the asset count, URL and run conclusion carried through.
#   2. Failed run conclusion — rc 1, ok false, naming the conclusion.
#      Conjunct 1 is not satisfiable by the release alone.
#   3. Run still in flight (`null` conclusion) — rc 2, empty stdout. A
#      pre-terminal run is never reported as a failed publish.
#   4. No release at the tag (HTTP 404) — rc 1, ok false, a reason
#      naming the tag. A definitive no, never an error.
#   5. Draft release — rc 1. A green run that left a draft behind is not
#      a landed publication.
#   6. Zero assets — rc 1. An empty release never passes vacuously.
#   7. An asset still uploading — rc 1, naming the counts.
#   8. Tag mismatch — the payload reports a different tag; rc 1.
#   9. Every definitive no writes an actionable stderr diagnostic
#      alongside its stdout envelope.
#  10. A tag carrying a double quote and a backslash emits VALID JSON
#      that round-trips through jq, and a value json_escape refuses
#      exits 2 with stdout untouched on both the deny and the ok path.
#  11. Auth or network failure on either call — rc 2, empty stdout.
#      Indeterminate is never reported as absent (fail closed).
#  12. Unparseable payload — rc 2, empty stdout.
#  13. Argument validation — wrong count, empty arguments and a
#      non-positive-integer run id exit 2.
#  14. Missing gh — exit 2 with an install hint.
#
# Approach: source the script (the main() guard prevents auto-run when
# sourced) and override `gh` as a transport mock covering both calls the
# script makes — `gh run view` for the conclusion and `gh api` for the
# release. Each case writes a fixture holding a raw
# `GET /repos/{o}/{r}/releases/tags/{tag}` body, and the mock runs the
# caller's own `--jq` filter over it with jq, the way gh does. The
# script's conjunction is what the fixtures exercise.
#
# Run: bash skills/release/tests/test_verify_github_release.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/verify-github-release.sh"
[[ -x "$SCRIPT" ]] || { echo "fatal: verify-github-release.sh not executable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq is required — the transport mock filters fixtures with it" >&2; exit 2; }

# shellcheck disable=SC1090  # ShellCheck cannot resolve the dynamically constructed source path.
source "$SCRIPT" || true
set +e

FAIL_COUNT=0
PASS_COUNT=0

OWNER=jbaruch
REPO=good-oss-citizen
TAG=v0.1.4
RUN_ID=34188269042

TMPDIR_TEST=$(mktemp -d "${TMPDIR:-/tmp}/verify-gh-release-test.XXXXXX") || { echo "fatal: could not create the test temporary directory" >&2; exit 2; }
cleanup_tmp() {
  if [[ -n "${TMPDIR_TEST:-}" ]]; then
    if ! rm -rf "$TMPDIR_TEST"; then
      echo "warning: could not remove temp dir ${TMPDIR_TEST} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_tmp EXIT
export MOCK_BODY_FILE="$TMPDIR_TEST/body.json"
export MOCK_MODE_FILE="$TMPDIR_TEST/mode"
export MOCK_RUN_FILE="$TMPDIR_TEST/run-mode"

# Own every file the helper creates, including on a failed assertion.
# Explicit -p also works on macOS versions whose mktemp -t ignores TMPDIR.
mkdir "$TMPDIR_TEST/script-tmp" || exit 2
mktemp() { command mktemp -p "$TMPDIR_TEST/script-tmp" "$@"; }

# The harness owns its EXIT trap; each invocation must install the
# helper's trap in the subshell that owns VGR_ERR_FILE. Preserve main's
# return/exit status so the existing success and failure checks still gate.
invoke_main() (
  trap cleanup_vgr_err_file EXIT
  main "$@"
)

assert_no_script_temps() {
  local remaining
  remaining=$(find "$TMPDIR_TEST/script-tmp" -mindepth 1 -print) || return 1
  assert_eq "helper temporary files after invocation" "" "$remaining"
}

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@" && assert_no_script_temps; then
    PASS_COUNT=$((PASS_COUNT + 1))
    echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    echo "  FAIL: $name" >&2
  fi
}

# Mock `gh` — transport stand-in for both calls the script makes.
# `gh run view ... --jq .conclusion` returns whatever MOCK_RUN_FILE
# holds (`ERROR` reproduces a non-zero exit with gh's stderr shape).
# `gh api <path> --jq '<filter>'` is driven by MOCK_MODE_FILE: `ok`
# serves the fixture body through the caller's own filter, `404` and
# `auth` reproduce gh's non-zero exit and its stderr text.
gh() {
  if [[ "$1" == "run" && "$2" == "view" ]]; then
    local run_mode
    run_mode=$(cat "$MOCK_RUN_FILE")
    if [[ "$run_mode" == "ERROR" ]]; then
      echo "gh: HTTP 401: Bad credentials" >&2
      return 1
    fi
    echo "$run_mode"
    return 0
  fi
  [[ "$1" == "api" ]] || { echo "mock gh: unexpected invocation: $*" >&2; return 99; }
  if [[ -n "${MOCK_EXPECTED_ENDPOINT:-}" && "$2" != "$MOCK_EXPECTED_ENDPOINT" ]]; then
    echo "mock gh: wrong release endpoint: $2; expected $MOCK_EXPECTED_ENDPOINT" >&2
    return 99
  fi
  local mode
  mode=$(cat "$MOCK_MODE_FILE")
  case "$mode" in
    404)
      echo "gh: Not Found (HTTP 404)" >&2
      return 1
      ;;
    auth)
      echo "gh: HTTP 401: Bad credentials" >&2
      return 1
      ;;
  esac
  local filter="" prev="" arg
  for arg in "$@"; do
    [[ "$prev" == "--jq" ]] && filter="$arg"
    prev="$arg"
  done
  [[ -n "$filter" ]] || { echo "mock gh: no --jq filter passed" >&2; return 99; }
  jq -r "$filter" "$MOCK_BODY_FILE"
}

set_mode() { echo "$1" > "$MOCK_MODE_FILE"; }
set_run() { echo "$1" > "$MOCK_RUN_FILE"; }
# Default every case to a successful run so a release-side fixture
# exercises conjunct 2; cases about conjunct 1 override it.
set_body() { cat > "$MOCK_BODY_FILE"; set_mode ok; set_run success; }

ok_of() { echo "$1" | jq -r 'if has("ok") then (.ok|tostring) else empty end'; }
reason_of() { echo "$1" | jq -r 'if has("reason") then .reason else empty end'; }

# --- Test 1: published release with retrievable assets -----------------------
test_published_release() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [
    {"name": "good-oss-citizen-0.1.4.tar.gz", "state": "uploaded"},
    {"name": "good-oss-citizen-0.1.4.tar.gz.sha256", "state": "uploaded"}
  ]
}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "0" "$rc" || return 1
  assert_eq "ok" "true" "$(ok_of "$out")" || return 1
  assert_eq "asset count" "2" "$(echo "$out" | jq -r '.assets')" || return 1
  assert_eq "url" "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}" "$(echo "$out" | jq -r '.url')" || return 1
  assert_eq "run conclusion" "success" "$(echo "$out" | jq -r '.run_conclusion')" || return 1
}
run "published release with uploaded assets confirms the publication" test_published_release

# --- Test 2: failed run conclusion -------------------------------------------
test_failed_run_conclusion() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  set_run failure
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  assert_eq "run conclusion" "failure" "$(echo "$out" | jq -r '.run_conclusion')" || return 1
  [[ "$(reason_of "$out")" == *"concluded failure"* ]] || { echo "    FAIL: reason should name the conclusion, got: $(reason_of "$out")" >&2; return 1; }
}
run "a retrievable release does not excuse a failed publish run" test_failed_run_conclusion

# --- Test 3: run still in flight ---------------------------------------------
test_run_in_flight() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  set_run null
  local out stderr rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>"$TMPDIR_TEST/err") || rc=$?
  stderr=$(cat "$TMPDIR_TEST/err")
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
  [[ "$stderr" == *"gh run watch"* ]] || { echo "    FAIL: stderr should point at the watch, got: ${stderr}" >&2; return 1; }
}
run "a run still in flight is indeterminate, not a failed publish" test_run_in_flight

# --- Test 4: run lookup failure ----------------------------------------------
test_run_lookup_failure() {
  set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": []}
JSON
  set_run ERROR
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
}
run "an unreadable run is indeterminate" test_run_lookup_failure

# --- Test 5: no release at the tag -------------------------------------------
test_missing_release() {
  set_mode 404
  set_run success
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"$TAG"* ]] || { echo "    FAIL: reason should name the tag, got: $(reason_of "$out")" >&2; return 1; }
}
run "absent release is a definitive no, not an error" test_missing_release

# --- Test 3: draft release ----------------------------------------------------
test_draft_release() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": true,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"draft"* ]] || { echo "    FAIL: reason should name the draft state, got: $(reason_of "$out")" >&2; return 1; }
}
run "draft release does not confirm a publication" test_draft_release

# --- Test 4: zero assets ------------------------------------------------------
test_no_assets() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": []
}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"no assets"* ]] || { echo "    FAIL: reason should name the empty asset list, got: $(reason_of "$out")" >&2; return 1; }
}
run "release with no assets never passes vacuously" test_no_assets

# --- Test 5: an asset still uploading ----------------------------------------
test_asset_not_uploaded() {
  set_body <<JSON
{
  "tag_name": "${TAG}",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/${TAG}",
  "assets": [
    {"name": "pkg.tar.gz", "state": "uploaded"},
    {"name": "pkg.tar.gz.sha256", "state": "starter"}
  ]
}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  assert_eq "ok" "false" "$(ok_of "$out")" || return 1
  [[ "$(reason_of "$out")" == *"1 of 2"* ]] || { echo "    FAIL: reason should name the counts, got: $(reason_of "$out")" >&2; return 1; }
}
run "an asset outside the uploaded state blocks confirmation" test_asset_not_uploaded

# --- Test 6: tag mismatch -----------------------------------------------------
test_tag_mismatch() {
  set_body <<JSON
{
  "tag_name": "v0.1.5",
  "draft": false,
  "html_url": "https://github.com/${OWNER}/${REPO}/releases/tag/v0.1.5",
  "assets": [{"name": "pkg.tar.gz", "state": "uploaded"}]
}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  [[ "$(reason_of "$out")" == *"v0.1.5"* ]] || { echo "    FAIL: reason should name the tag actually returned, got: $(reason_of "$out")" >&2; return 1; }
}
run "a release reporting another tag is refused" test_tag_mismatch

# --- Test 7: auth failure is indeterminate -----------------------------------
test_auth_failure_indeterminate() {
  set_mode auth
  set_run success
  local out stderr rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>"$TMPDIR_TEST/err") || rc=$?
  stderr=$(cat "$TMPDIR_TEST/err")
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
  [[ "$stderr" == *"gh auth status"* ]] || { echo "    FAIL: stderr should carry a recovery hint, got: ${stderr}" >&2; return 1; }
}
run "auth failure is indeterminate, never reported as absent" test_auth_failure_indeterminate

# --- Test 8: unparseable payload ---------------------------------------------
test_unparseable_payload() {
  set_body <<'JSON'
{"unexpected": "shape"}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
}
run "unparseable release payload is indeterminate" test_unparseable_payload

# --- Test 9: argument validation ---------------------------------------------
test_wrong_arg_count() {
  local stderr rc=0
  stderr=$(invoke_main "$OWNER" "$REPO" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"usage:"* ]] || { echo "    FAIL: stderr missing usage line, got: ${stderr}" >&2; return 1; }
}
run "wrong argument count exits 2 with usage" test_wrong_arg_count

test_empty_tag() {
  local stderr rc=0
  stderr=$(invoke_main "$OWNER" "$REPO" "" "$RUN_ID" 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"non-empty"* ]] || { echo "    FAIL: stderr should name the empty argument, got: ${stderr}" >&2; return 1; }
}
run "empty tag argument exits 2" test_empty_tag

test_bad_run_id() {
  local stderr rc=0
  stderr=$(invoke_main "$OWNER" "$REPO" "$TAG" 0 2>&1 >/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"positive integer"* ]] || { echo "    FAIL: stderr should reject the run id, got: ${stderr}" >&2; return 1; }
}
run "a run id of 0 exits 2" test_bad_run_id

# --- Every definitive no writes an actionable stderr diagnostic --------------
# Script Requirements calls for self-error-handling on stderr; the
# structured stdout envelope does not discharge it.
test_denials_carry_stderr_diagnostics() {
  local case_name rc stderr
  for case_name in failed-run absent draft empty partial mismatch; do
    case "$case_name" in
      failed-run)
        set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
        set_run failure ;;
      absent)   set_mode 404; set_run success ;;
      draft)
        set_body <<JSON
{"tag_name": "${TAG}", "draft": true, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
        ;;
      empty)
        set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": []}
JSON
        ;;
      partial)
        set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}, {"name": "q", "state": "starter"}]}
JSON
        ;;
      mismatch)
        set_body <<JSON
{"tag_name": "v9.9.9", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
        ;;
    esac
    rc=0
    # Subshell: `main` is sourced, so its `exit 1` would end the harness.
    ( invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" >/dev/null 2>"$TMPDIR_TEST/err" ) || rc=$?
    stderr=$(cat "$TMPDIR_TEST/err")
    assert_eq "${case_name} exit code" "1" "$rc" || return 1
    [[ -n "$stderr" ]] || { echo "    FAIL: ${case_name} wrote no stderr diagnostic" >&2; return 1; }
    [[ "$stderr" == *"verify-github-release.sh:"* ]] || { echo "    FAIL: ${case_name} diagnostic is unattributed, got: ${stderr}" >&2; return 1; }
  done
}
run "every definitive no writes an actionable stderr diagnostic" test_denials_carry_stderr_diagnostics

# --- A quote-bearing tag still emits valid JSON ------------------------------
# Git ref names permit a double quote, so raw interpolation would emit a
# broken envelope and silently defeat any wrapper that parses stdout.
test_quote_bearing_tag_emits_valid_json() {
  local weird='v1.0-"quoted"-\slash'
  set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$weird" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "1" "$rc" || return 1
  echo "$out" | jq -e . >/dev/null || { echo "    FAIL: envelope is not valid JSON: ${out}" >&2; return 1; }
  assert_eq "tag round-trips" "$weird" "$(echo "$out" | jq -r '.tag')" || return 1
}
run "a tag carrying a quote and a backslash emits valid JSON" test_quote_bearing_tag_emits_valid_json

# Tags travel as one URL path component; '#' must not become a fragment
# and percent escapes must not change which tag the API looks up.
test_encoded_tag_endpoint() {
  local tag='release/v1.0"quoted"#%2F-é' out rc=0
  git check-ref-format "refs/tags/$tag" || return 1
  # Dynamic scope confines this expectation to this test's invocation.
  local MOCK_EXPECTED_ENDPOINT="repos/${OWNER}/${REPO}/releases/tags/release%2Fv1.0%22quoted%22%23%252F-%C3%A9"
  jq -n --arg tag "$tag" '{tag_name: $tag, draft: false, html_url: "https://example.test/release", assets: [{name: "pkg.tar.gz", state: "uploaded"}]}' > "$MOCK_BODY_FILE" || return 1
  set_mode ok
  set_run success
  out=$(invoke_main "$OWNER" "$REPO" "$tag" "$RUN_ID" 2>"$TMPDIR_TEST/err") || rc=$?
  assert_eq "encoded tag exit code" "0" "$rc" || { cat "$TMPDIR_TEST/err" >&2; return 1; }
  assert_eq "encoded tag confirms" "true" "$(ok_of "$out")" || return 1
  assert_eq "encoded tag round-trips" "$tag" "$(echo "$out" | jq -r '.tag')" || return 1
}
run "release API lookup preserves reserved and UTF-8 tag bytes" test_encoded_tag_endpoint

# A failed printf conversion must stop the lookup without emitting a verdict.
# Exercise both conversion stages through main, including when errexit is
# disabled by the caller's checked command substitution.
test_encoding_failure_is_indeterminate() {
  local format out stderr rc
  set_body <<JSON
{"tag_name": "release/v1", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
  for format in '%d' '%%%02X'; do
    rc=0
    out=$(
      printf() {
        if [[ "${1:-}" == '-v' && "${3:-}" == "$format" ]]; then
          return 1
        fi
        # shellcheck disable=SC2059  # Forward the caller's printf format unchanged outside the injected failure.
        builtin printf "$@"
      }
      invoke_main "$OWNER" "$REPO" 'release/v1' "$RUN_ID" 2>"$TMPDIR_TEST/err"
    ) || rc=$?
    stderr=$(cat "$TMPDIR_TEST/err")
    assert_eq "encoding failure exit code ($format)" "2" "$rc" || return 1
    assert_eq "encoding failure emits no envelope ($format)" "" "$out" || return 1
    [[ "$stderr" == *"could not encode tag"* && "$stderr" == *"Bash"* ]] || {
      echo "    FAIL: encoding failure needs a Bash recovery hint, got: ${stderr}" >&2
      return 1
    }
  done
}
run "failed tag encoding is indeterminate with no envelope" test_encoding_failure_is_indeterminate

# --- An unescapable value is indeterminate, never a half-built envelope ------
# json_escape runs inside a command substitution, so a status the caller
# drops would leave the envelope built from an empty string and reported
# as a definitive no (rules/error-handling.md Shell Error Handling).
test_control_character_tag_is_indeterminate() {
  local weird
  weird=$(printf 'v1.0\tbad')
  set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$weird" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
}
run "a control character in the tag exits 2 with no envelope" test_control_character_tag_is_indeterminate

# The success path escapes through the same helper and owes the same
# propagation; a run whose conclusion carries a control character must
# not emit a truncated ok:true envelope.
test_control_character_conclusion_is_indeterminate() {
  set_body <<JSON
{"tag_name": "${TAG}", "draft": false, "html_url": "u", "assets": [{"name": "p", "state": "uploaded"}]}
JSON
  set_run "$(printf 'suc\tcess')"
  local out rc=0
  out=$(invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>/dev/null) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  assert_eq "stdout must stay empty" "" "$out" || return 1
}
run "an unescapable run conclusion exits 2 with no envelope" test_control_character_conclusion_is_indeterminate

# --- Test 10: missing gh ------------------------------------------------------
# Runs in a subshell with the mock removed and PATH emptied, so
# `command -v gh` finds neither the function nor a binary. The guard
# fires before mktemp, so an empty PATH is survivable here.
test_missing_gh() {
  local stderr rc=0
  stderr=$(
    unset -f gh
    # shellcheck disable=SC2123  # emptying the search path is the point: `command -v gh` must find neither the mock function nor a binary
    PATH=""
    invoke_main "$OWNER" "$REPO" "$TAG" "$RUN_ID" 2>&1 >/dev/null
  ) || rc=$?
  assert_eq "exit code" "2" "$rc" || return 1
  [[ "$stderr" == *"cli.github.com"* ]] || { echo "    FAIL: stderr should carry an install hint, got: ${stderr}" >&2; return 1; }
}
run "missing gh exits 2 with an install hint" test_missing_gh

echo
echo "results: ${PASS_COUNT} pass, ${FAIL_COUNT} fail"
exit "$FAIL_COUNT"
