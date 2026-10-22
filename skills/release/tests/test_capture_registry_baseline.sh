#!/usr/bin/env bash
# Outcome-based tests for capture-registry-baseline.sh.
#
# The contract under test: emit a baseline verify-publish-landed.sh can order,
# or the verified `unpublished` sentinel, or exit 2 — never an empty or
# guessed baseline. An empty baseline is the dangerous output: it flows into
# verify-publish-landed.sh's conjunct 2, where every version compares as
# "advanced past empty", so an unpublished release confirms.
#
# The unpublished cases are both directions of one guard: the versions API's
# structured not-found body is a first-publish baseline, while a 404 of any
# other shape, a 401, a 500 and a transport failure stay exit 2.
#
# Approach: run the script as a subprocess with a fake `tessl` executable
# placed first on PATH (real jq stays reachable), so the real
# `set -euo pipefail`, the EXIT trap, and the exit codes are all exercised
# end-to-end. The fake answers only `tessl api v1/tiles/<ws>/<tile>/versions`
# and selects its fixture via MOCK_MODE. Stdout is asserted literally.
#
# Run: bash skills/release/tests/test_capture_registry_baseline.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/capture-registry-baseline.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: capture-registry-baseline.sh not readable at $SCRIPT" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

# `set -e` is deliberately off here (a failure-counting harness must not die
# on its first red assertion), so an mktemp failure would otherwise leave
# STUBDIR empty, put "" on PATH, and cascade confusing assertion failures.
STUBDIR="$(mktemp -d)" || { echo "fatal: mktemp -d failed — cannot stage the tessl fake; check TMPDIR is writable" >&2; exit 2; }
[[ -n "$STUBDIR" && -d "$STUBDIR" ]] || { echo "fatal: mktemp -d returned no usable directory (got '${STUBDIR}')" >&2; exit 2; }
# Named handler ending `return 0`: the EXIT trap's final command status
# becomes the process's exit status, so a failed cleanup would turn an
# all-green run non-zero.
cleanup_stubdir() {
  if [[ -n "${STUBDIR:-}" ]]; then
    if ! rm -rf "$STUBDIR"; then
      echo "warning: could not remove stub dir ${STUBDIR} — remove it by hand" >&2
    fi
  fi
  return 0
}
trap cleanup_stubdir EXIT
export PATH="$STUBDIR:$PATH"

# Fake tessl. Bodies mirror the real versions API: a never-published plugin
# answers the structured not-found body with exit 1 (observed live with
# `tessl api v1/tiles/gamussa/<absent>/versions`).
cat > "$STUBDIR/tessl" <<'STUB' || { echo "fatal: cannot write the tessl fake to ${STUBDIR}/tessl — check that TMPDIR is writable and not full" >&2; exit 2; }
#!/usr/bin/env bash
[[ "${1:-}" == api && "${2:-}" == "v1/tiles/gamussa/coding-policy/versions" ]] \
  || { echo "fake tessl: unsupported invocation: $*" >&2; exit 98; }
case "${MOCK_MODE:-}" in
  ok)
    printf '{"data":[{"attributes":{"version":"0.3.9"}},{"attributes":{"version":"0.3.91"}}]}\n' ;;
  warning_on_stderr)
    echo "warn: registry cache stale, refetching" >&2
    printf '{"data":[{"attributes":{"version":"0.3.91"}}]}\n' ;;
  empty)
    printf '{"data":[]}\n' ;;
  absent)
    printf '{\n  "error": {\n    "title": "Not Found",\n    "status": 404,\n    "message": "Not Found"\n  }\n}\n'
    exit 1 ;;
  absent_rc2|absent_rc137)
    printf '{"error":{"title":"Not Found","status":404,"message":"Not Found"}}\n'
    exit "${MOCK_MODE#absent_rc}" ;;
  entry_no_version)
    printf '%s\n' '{"data":[{"attributes":{}}]}' ;;
  entry_null_version)
    printf '%s\n' '{"data":[{"attributes":{"version":null}}]}' ;;
  entry_numeric_version)
    printf '%s\n' '{"data":[{"attributes":{"version":123}}]}' ;;
  entry_no_attributes)
    printf '%s\n' '{"data":[{}]}' ;;
  entry_not_object)
    printf '%s\n' '{"data":[1]}' ;;
  entry_mixed)
    printf '%s\n' '{"data":[{"attributes":{"version":"0.1.0"}},{"attributes":{}}]}' ;;
  absent_404_bare)
    printf '{"error":{"status":404}}\n'
    exit 1 ;;
  absent_404_extra_key)
    printf '{"error":{"title":"Not Found","status":404},"links":{"self":"x"}}\n'
    exit 1 ;;
  absent_404_string_status)
    printf '{"error":{"title":"Not Found","status":"404"}}\n'
    exit 1 ;;
  unauthorized)
    printf '{"error":{"title":"Unauthorized","status":401,"message":"Unauthorized"}}\n'
    exit 1 ;;
  server_error)
    printf '{"error":{"title":"Internal Server Error","status":500,"message":"Internal Server Error"}}\n'
    exit 1 ;;
  network_failure)
    echo "error: fetch failed: getaddrinfo ENOTFOUND api.tessl.io" >&2
    exit 1 ;;
  non_json)
    printf '<html>502 Bad Gateway</html>\n' ;;
  no_data)
    printf '{}\n' ;;
  version_not_numeric)
    printf '{"data":[{"attributes":{"version":"unavailable"}}]}\n' ;;
  version_prerelease)
    printf '{"data":[{"attributes":{"version":"1.2.3-rc.1+build.5"}}]}\n' ;;
  *) echo "fake tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
esac
STUB
chmod +x "$STUBDIR/tessl" || { echo "fatal: cannot make ${STUBDIR}/tessl executable — check the filesystem allows chmod there (TMPDIR mounted noexec?)" >&2; exit 2; }

invoke() {
  OUT="$(MOCK_MODE="$1" bash "$SCRIPT" gamussa coding-policy 2>"$STUBDIR/err")"
  CODE=$?
  ERR="$(cat "$STUBDIR/err")" || { echo "fatal: cannot read captured stderr at ${STUBDIR}/err — check that TMPDIR is readable" >&2; exit 2; }
}

expect_value() { # <mode> <expected stdout> <label>
  invoke "$1"
  if [[ "$CODE" == 0 && "$OUT" == "$2" ]]; then pass "$3"; else fail "$3: code=$CODE out='$OUT' err='$ERR'"; fi
}

expect_refusal() { # <mode> <label>
  invoke "$1"
  if [[ "$CODE" == 2 && -z "$OUT" && -n "$ERR" ]]; then pass "$2"; else fail "$2: code=$CODE out='$OUT' err='$ERR'"; fi
}

echo "capture-registry-baseline.sh tests"

expect_value ok '{"version":"0.3.91"}' "published -> numeric max version"
expect_value warning_on_stderr '{"version":"0.3.91"}' "stderr warning does not poison the version"

# Accept: a verified never-published plugin is a first-publish baseline.
expect_value absent '{"version":"unpublished"}' "structured Not Found/404 (exit 1) -> unpublished"
expect_value empty '{"version":"unpublished"}' "empty versions array -> unpublished"

# Reject: anything short of the exact not-found shape stays an error.
expect_refusal absent_rc2 "not-found body with exit 2 -> exit 2, never unpublished"
expect_refusal absent_rc137 "not-found body with exit 137 (killed) -> exit 2"
expect_refusal entry_no_version "malformed entry (entry_no_version) -> exit 2, never unpublished"
expect_refusal entry_null_version "malformed entry (entry_null_version) -> exit 2, never unpublished"
expect_refusal entry_numeric_version "malformed entry (entry_numeric_version) -> exit 2, never unpublished"
expect_refusal entry_no_attributes "malformed entry (entry_no_attributes) -> exit 2, never unpublished"
expect_refusal entry_not_object "malformed entry (entry_not_object) -> exit 2, never unpublished"
expect_refusal entry_mixed "malformed entry (entry_mixed) -> exit 2, never unpublished"
expect_refusal absent_404_bare "404 without the Not Found title -> exit 2"
expect_refusal absent_404_extra_key "404 body with an extra top-level key -> exit 2"
expect_refusal absent_404_string_status "404 as a string status -> exit 2"
expect_refusal unauthorized "401 -> exit 2, never unpublished"
expect_refusal server_error "500 -> exit 2, never unpublished"
expect_refusal network_failure "transport failure (no body) -> exit 2"
expect_refusal non_json "non-JSON body -> exit 2"
expect_refusal no_data "JSON without .data -> exit 2"
expect_refusal version_not_numeric "non-numeric version -> exit 2"
# The comparator (version_gt) cannot order a prerelease/build value.
expect_refusal version_prerelease "prerelease+build version -> exit 2"

invoke network_failure
if [[ "$ERR" == *"ENOTFOUND"* ]]; then pass "transport failure surfaces tessl's stderr"; else fail "transport stderr: err='$ERR'"; fi

# --- wrong arity is a usage error, not a silent default ---
OUT="$(bash "$SCRIPT" gamussa 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage"
else
  fail "arity: code=$CODE out=$OUT err=$ERR"
fi

echo ""
echo "capture-registry-baseline.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
echo "test-summary: passed=${PASS_COUNT} failed=${FAIL_COUNT} token=${SUMMARY_TOKEN}"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
