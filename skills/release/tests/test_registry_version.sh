#!/usr/bin/env bash
# Outcome-based tests for registry-version.sh.
#
# The contract: read the tile's latest published version from the versions API
# and emit it as JSON, distinguishing three outcomes — a published version
# ({"version":"x.y.z"}), a never-published tile ({"version":null}, exit 0), and
# a tool/parse failure (exit 2, empty stdout, diagnostic on stderr). The
# load-bearing properties are (a) NUMERIC max selection (0.3.119 outranks
# 0.3.20), (b) a tessl stderr warning must not poison the parse, and (c) a
# non-JSON body / tessl failure must exit 2, never emit a bogus version.
#
# Approach: run the script as a subprocess with a `tessl` fake first on PATH
# (real jq stays reachable), so the real `set -euo pipefail`, the EXIT trap,
# and the exit codes are exercised end-to-end. The fake selects its fixture
# via MOCK_MODE, exported through the invocation so it reaches the fake.
#
# Run: bash skills/release/tests/test_registry_version.sh
# Exit 0 on all-pass; 1 with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/registry-version.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: registry-version.sh not readable at $SCRIPT" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

# `set -e` is deliberately off (a failure-counting harness must not die on its
# first red), so guard mktemp -d explicitly: an empty STUBDIR would put "" on
# PATH and cascade confusing failures.
STUBDIR="$(mktemp -d)" || { echo "fatal: mktemp -d failed — cannot stage the tessl stub; check TMPDIR" >&2; exit 2; }
[[ -n "$STUBDIR" && -d "$STUBDIR" ]] || { echo "fatal: mktemp -d returned no usable directory (got '${STUBDIR}')" >&2; exit 2; }
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

# Fake tessl. Handles `tessl api <endpoint>`; selects a fixture via MOCK_MODE.
# The `ok` fixture lists versions oldest-first with a double-digit and a
# triple-digit patch so the script's NUMERIC max selection is genuinely
# exercised (a lexical sort would pick 0.3.20).
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  api)
    case "${MOCK_MODE:-}" in
      ok)
        printf '{"data":[{"attributes":{"version":"0.3.20"}},{"attributes":{"version":"0.3.119"}},{"attributes":{"version":"0.0.1"}}]}\n' ;;
      empty)
        printf '{"data":[]}\n' ;;
      warning_on_stderr)
        echo "warn: registry cache stale, refetching" >&2
        printf '{"data":[{"attributes":{"version":"0.3.31"}}]}\n' ;;
      non_json)
        printf '<html>502 Bad Gateway</html>\n' ;;
      tessl_fails)
        echo "error: not authenticated" >&2
        exit 1 ;;
      no_data)
        printf '{}\n' ;;
      malformed_version)
        printf '{"data":[{"attributes":{"version":"not-a-version"}}]}\n' ;;
      *) echo "stub tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
    esac
    ;;
  *) echo "stub tessl: unsupported invocation: $*" >&2; exit 2 ;;
esac
STUB
chmod +x "$STUBDIR/tessl"

# Invoke with MOCK_MODE exported so it reaches the fake tessl subprocess.
invoke() {
  local mode="$1"
  OUT="$(MOCK_MODE="$mode" bash "$SCRIPT" gAmUssA coding-policy 2>"$STUBDIR/err")"
  CODE=$?
  ERR="$(cat "$STUBDIR/err")"
}

echo "== registry-version.sh tests =="

# --- happy path: NUMERIC max across the page, emitted as JSON ---
invoke ok
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"version":"0.3.119"}' ]]; then
  pass "numeric max selection: 0.3.20 / 0.3.119 / 0.0.1 -> {\"version\":\"0.3.119\"}"
else
  fail "happy path: code=$CODE out='$OUT' err='$ERR'"
fi

# --- never published: empty .data -> {"version":null}, exit 0 (valid baseline) ---
invoke empty
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"version":null}' ]]; then
  pass "never-published (empty data) -> exit 0, {\"version\":null}"
else
  fail "empty data: code=$CODE out='$OUT' err='$ERR'"
fi

# --- a tessl warning on stderr must not poison the parsed version ---
invoke warning_on_stderr
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"version":"0.3.31"}' ]]; then
  pass "stderr warning does not poison the parsed version"
else
  fail "stderr warning: code=$CODE out='$OUT' err='$ERR'"
fi

# --- non-JSON body -> exit 2, 'not valid JSON', NO bogus version emitted ---
invoke non_json
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not valid JSON"* ]]; then
  pass "non-JSON body -> exit 2, empty stdout, actionable diagnostic"
else
  fail "non-JSON: code=$CODE out='$OUT' err='$ERR'"
fi

# --- tessl itself failing -> exit 2 with the tool's stderr surfaced ---
invoke tessl_fails
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not authenticated"* ]]; then
  pass "tessl failure -> exit 2, tool stderr surfaced"
else
  fail "tessl failure: code=$CODE out='$OUT' err='$ERR'"
fi

# --- valid JSON with no .data array ({}) -> exit 2, NOT a fabricated empty
#     baseline (a registry error body must not read as never-published) ---
invoke no_data
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"no .data array"* ]]; then
  pass "no .data array ({}) -> exit 2 (not a fabricated first-publish baseline)"
else
  fail "no-data: code=$CODE out='$OUT' err='$ERR'"
fi

# --- a non-numeric latest version -> exit 2 (a version_gt input must be numeric) ---
invoke malformed_version
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not numeric"* ]]; then
  pass "malformed (non-numeric) version -> exit 2"
else
  fail "malformed-version: code=$CODE out='$OUT' err='$ERR'"
fi

# --- wrong arity is a usage error, not a silent default ---
OUT="$(bash "$SCRIPT" gAmUssA 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage"
else
  fail "arity: code=$CODE out='$OUT' err='$ERR'"
fi

echo ""
echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
