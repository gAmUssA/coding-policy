#!/usr/bin/env bash
# Outcome-based tests for capture-registry-baseline.sh.
#
# The contract under test is the one the pasted pipeline in SKILL.md could
# not hold: a parse miss or a tool failure must exit 2 with a diagnostic,
# never emit an empty baseline. An empty baseline is the dangerous output —
# it flows into verify-publish-landed.sh's conjunct 2, where every version
# compares as "advanced past empty", so an unpublished release confirms.
#
# Approach: run the script as a subprocess with a stub `tessl` executable
# placed first on PATH, so the real `set -euo pipefail`, the EXIT trap, and
# the exit codes are all exercised end-to-end — a sourced-function harness
# would approximate them and miss exactly the trap/exit-status defects this
# script hit in development. The stub selects its fixture via MOCK_MODE.
# jq is not used by the script, so stdout is asserted literally.
#
# Run: bash skills/release/tests/test_capture_registry_baseline.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/capture-registry-baseline.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: capture-registry-baseline.sh not readable at $SCRIPT" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

# Invoke the script as a subprocess with a stubbed `tessl` on PATH, so the
# real `set -euo pipefail` and exit codes are exercised end-to-end rather
# than the sourced-function approximation of them.
# `set -e` is deliberately off here (a failure-counting harness must not die
# on its first red assertion), so an mktemp failure would otherwise leave
# STUBDIR empty, put "" on PATH, and produce a cascade of confusing
# assertion failures that look like the script's fault.
STUBDIR="$(mktemp -d)" || { echo "fatal: mktemp -d failed — cannot stage the tessl stub; check TMPDIR is writable" >&2; exit 2; }
[[ -n "$STUBDIR" && -d "$STUBDIR" ]] || { echo "fatal: mktemp -d returned no usable directory (got '${STUBDIR}')" >&2; exit 2; }
# Named handler ending `return 0`, not a bare `trap 'rm -rf ...'`: the EXIT
# trap's final command status becomes the process's exit status, so a failed
# cleanup (permissions, transient FS error) would turn an all-green run
# non-zero and flake CI. `set -e` is off here, so the rm cannot abort the
# handler — but its status still speaks for the run unless `return 0` does.
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

# MOCK_MODE selects the fixture the stub emits.
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
case "${MOCK_MODE:-}" in
  ok)
    echo "Plugin gAmUssA/coding-policy"
    echo "  Latest Version   0.3.91"
    ;;
  warning_on_stderr)
    echo "warn: registry cache stale, refetching" >&2
    echo "  Latest Version   0.3.91"
    ;;
  no_version_line)
    echo "Plugin gAmUssA/coding-policy"
    echo "  Description   nothing useful here"
    ;;
  version_not_semver)
    # The registry reporting an outage on the line we parse. Taking the
    # last field on faith yielded {"version":"unavailable"} — a baseline
    # verify-publish-landed.sh's version comparison cannot order.
    echo "Plugin gAmUssA/coding-policy"
    echo "  Latest Version   unavailable"
    ;;
  version_is_prose)
    echo "  Latest Version   see registry status page"
    ;;
  tessl_fails)
    echo "error: not authenticated" >&2
    exit 1
    ;;
  *) echo "stub tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
esac
STUB
chmod +x "$STUBDIR/tessl"

invoke() { OUT="$(bash "$SCRIPT" gAmUssA coding-policy 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"; }

echo "capture-registry-baseline.sh tests"

# --- happy path: version parsed, emitted as JSON ---
MOCK_MODE=ok invoke
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"version":"0.3.91"}' ]]; then
  pass "parses Latest Version -> {\"version\":\"0.3.91\"}"
else
  fail "happy path: code=$CODE out=$OUT err=$ERR"
fi

# --- a tessl warning on stderr must not poison the parse ---
# This is the failure the pasted `tessl plugin info | grep | awk` could not
# see: with stdout and stderr merged, the warning line reaches the parse.
MOCK_MODE=warning_on_stderr invoke
if [[ "$CODE" == 0 ]] && [[ "$OUT" == '{"version":"0.3.91"}' ]]; then
  pass "stderr warning does not poison the parsed version"
else
  fail "stderr warning: code=$CODE out=$OUT err=$ERR"
fi

# --- parse miss: exit 2, diagnostic, and NO empty baseline on stdout ---
# The pasted pipeline yielded an empty PRE here, which then satisfied
# conjunct 2 vacuously.
MOCK_MODE=no_version_line invoke
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"could not parse 'Latest Version'"* ]]; then
  pass "parse miss -> exit 2, empty stdout, actionable diagnostic"
else
  fail "parse miss: code=$CODE out=$OUT err=$ERR"
fi

# --- a non-version token on the Latest Version line is exit 2, not a value ---
# The contract promises numeric major.minor.patch. Emitting whatever the
# registry printed hands verify-publish-landed.sh a baseline its comparator
# cannot order — a registry outage silently becomes a bogus conjunct-2
# verdict.
MOCK_MODE=version_not_semver invoke
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not a numeric major.minor.patch"* ]]; then
  pass "non-version token ('unavailable') -> exit 2, empty stdout"
else
  fail "non-semver: code=$CODE out=$OUT err=$ERR"
fi

# --- multi-word prose on the version line is also rejected ---
# awk '{print $NF}' takes the LAST field, so prose yields a plausible-looking
# single token ("page") that only a shape check catches.
MOCK_MODE=version_is_prose invoke
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not a numeric major.minor.patch"* ]]; then
  pass "prose version line -> exit 2, empty stdout"
else
  fail "prose version: code=$CODE out=$OUT err=$ERR"
fi

# --- a prerelease/build version is REJECTED, not accepted ---
# The accepted shape is bounded by the consumer, not by semver. This
# baseline's only reader is verify-publish-landed.sh's version_gt(), which
# splits on '.' and feeds the fields to bash arithmetic: `1.2.3-rc.1+build.5`
# arrives at `(( ))` as `3-rc.1+build.5`, and the arithmetic error makes the
# comparison return "not greater" instead of raising — so a landed publish
# reports as "Latest Version is not greater than baseline — investigate the
# registry state". Accepting a shape the consumer cannot order buys a false
# release verdict, so this script rejects it at the boundary.
cat > "$STUBDIR/tessl" <<'STUB2'
#!/usr/bin/env bash
echo "  Latest Version   1.2.3-rc.1+build.5"
STUB2
chmod +x "$STUBDIR/tessl"
invoke
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not a numeric major.minor.patch"* ]]; then
  pass "prerelease+build version -> exit 2 (comparator cannot order it)"
else
  fail "prerelease rejection: code=$CODE out=$OUT err=$ERR"
fi
# restore the MOCK_MODE stub for the remaining cases
cat > "$STUBDIR/tessl" <<'STUB'
#!/usr/bin/env bash
case "${MOCK_MODE:-}" in
  ok)
    echo "Plugin gAmUssA/coding-policy"
    echo "  Latest Version   0.3.91"
    ;;
  tessl_fails)
    echo "error: not authenticated" >&2
    exit 1
    ;;
  *) echo "stub tessl: unknown MOCK_MODE='${MOCK_MODE:-}'" >&2; exit 99 ;;
esac
STUB
chmod +x "$STUBDIR/tessl"

# --- tessl itself failing is exit 2 with the tool's stderr surfaced ---
MOCK_MODE=tessl_fails invoke
if [[ "$CODE" == 2 ]] && [[ -z "$OUT" ]] && [[ "$ERR" == *"not authenticated"* ]]; then
  pass "tessl failure -> exit 2, tool stderr surfaced"
else
  fail "tessl failure: code=$CODE out=$OUT err=$ERR"
fi

# --- wrong arity is a usage error, not a silent default ---
OUT="$(bash "$SCRIPT" gAmUssA 2>"$STUBDIR/err")"; CODE=$?; ERR="$(cat "$STUBDIR/err")"
if [[ "$CODE" == 2 ]] && [[ "$ERR" == *"usage:"* ]]; then
  pass "wrong arity -> exit 2 with usage"
else
  fail "arity: code=$CODE out=$OUT err=$ERR"
fi

echo ""
echo "capture-registry-baseline.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
