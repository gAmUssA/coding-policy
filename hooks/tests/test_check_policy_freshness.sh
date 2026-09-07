#!/usr/bin/env bash
# Outcome-based tests for check-policy-freshness.sh.
#
# The hook shells out to `tessl outdated --json`, so tests put a stub `tessl` on
# PATH (real jq/bash/date stay resolvable) and drive it via STUB_JSON / STUB_EXIT.
#
# Covers:
#   1. Outdated present  -> emits marker additionalContext naming the plugin + new version.
#   2. Nothing outdated  -> emits a marker "policy: fresh" status, exit 0.
#   3. Throttle          -> with an injected fixed clock (FRESHNESS_NOW): a call
#                           inside the window is silent (throttle skips the check
#                           before any emit), a call past it fires again.
#   4. tessl missing     -> silent no-op, exit 0 (no crash).
#   5. tessl errors      -> silent no-op, exit 0 (network/registry failure tolerated).
#
# Run: bash hooks/tests/test_check_policy_freshness.sh
set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/check-policy-freshness.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: hook not found/readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required for these tests" >&2; exit 2; }

TMP="$(mktemp -d -t freshness-test.XXXXXX)" || { echo "fatal: mktemp failed" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
trap cleanup EXIT

# A stub `tessl` that echoes $STUB_JSON for `outdated`, or exits $STUB_EXIT.
STUBBIN="$TMP/bin"; mkdir -p "$STUBBIN"
cat > "$STUBBIN/tessl" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "outdated" ]]; then
  [[ -n "${STUB_EXIT:-}" ]] && exit "$STUB_EXIT"
  printf '%s' "${STUB_JSON:-}"
fi
STUB
chmod +x "$STUBBIN/tessl"

OUTDATED='{"outdated":[{"current":{"tile":{"workspaceName":"gamussa","tileName":"coding-policy","version":"0.3.138"}},"update":{"version":"0.3.139"}}]}'
EMPTY='{"outdated":[]}'

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# run <state-dir> [extra env assignments...] -> OUT, RC  (stub tessl on PATH)
run() {
  local dir="$1"; shift
  OUT="$(env "PATH=$STUBBIN:$PATH" FRESHNESS_STATE_DIR="$dir" "$@" bash "$SCRIPT" </dev/null 2>/dev/null)"
  RC=$?
}

# 1. outdated present -> marker notice
d="$TMP/c1"
run "$d" STUB_JSON="$OUTDATED"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("coding-policy") and test("0.3.139")' >/dev/null 2>&1; then
  pass; else fail "outdated present: expected marker notice, got RC=$RC OUT=$OUT"; fi

# 2. nothing outdated -> marker "policy: fresh" status
d="$TMP/c2"
run "$d" STUB_JSON="$EMPTY"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("Session-start status") and test("fresh")' >/dev/null 2>&1; then
  pass; else fail "empty outdated: expected fresh status, got RC=$RC OUT=$OUT"; fi

# 3. throttle -> deterministic via an injected clock (FRESHNESS_NOW), not the
#    real wall clock. First call stamps t; a call 1h later is throttled; a call
#    25h later (past the 24h window) fires again.
d="$TMP/c3"
run "$d" STUB_JSON="$OUTDATED" FRESHNESS_NOW=1000000            # fires, stamps 1000000
[[ -n "$OUT" ]] || fail "throttle setup: first call should have fired"
run "$d" STUB_JSON="$OUTDATED" FRESHNESS_NOW=1003600            # +1h, inside 24h window
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "throttle active: call inside window should be silent, got OUT=$OUT"; fi
run "$d" STUB_JSON="$OUTDATED" FRESHNESS_NOW=1090000            # +25h, past window
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext' >/dev/null 2>&1; then pass; else fail "throttle expired: call past window should fire, got OUT=$OUT"; fi

# 4. tessl missing -> silent no-op. Build a minimal PATH with symlinks to the
#    tools the hook needs (jq present so it passes that check) but NO tessl, so
#    the test is environment-independent rather than assuming /usr/bin contents.
minbin="$TMP/minbin"; mkdir -p "$minbin"
for t in bash jq date mkdir; do ln -s "$(command -v "$t")" "$minbin/$t"; done
d="$TMP/c4"
OUT="$(env "PATH=$minbin" FRESHNESS_STATE_DIR="$d" bash "$SCRIPT" </dev/null 2>/dev/null)"; RC=$?
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "tessl missing: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 5. tessl errors -> silent no-op
d="$TMP/c5"
run "$d" STUB_JSON="$OUTDATED" STUB_EXIT=1
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "tessl error: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

# 6. malformed injected clock -> no-op, exit 0 (never aborts SessionStart).
d="$TMP/c6"
run "$d" STUB_JSON="$OUTDATED" FRESHNESS_NOW="not-a-number"
if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "invalid clock: expected silent exit 0, got RC=$RC OUT=$OUT"; fi

echo "─────────────────────────────────────────────" >&2
if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
echo "PASSED: all ${PASS} checks" >&2
