#!/usr/bin/env bash
# Outcome-based tests for version-compare.sh's version_gt().
#
# version_gt is the comparator every release-gate script leans on: it decides
# "did the registry advance past the baseline". The load-bearing property is
# NUMERIC (not lexical) ordering — 0.3.119 must outrank 0.3.20, which a string
# compare gets backwards ('1' < '2') — plus a total, well-defined verdict on
# equality, downgrades, and empty/short components (empty = a never-published
# first-publish baseline that anything real must outrank).
#
# Approach: source the helper (no top-level side effects) and call version_gt
# directly, plus run the script as a subprocess to exercise the direct-execution
# CLI guard's exit-code contract. `set -uo pipefail`, not `-e`: a failure-
# counting harness must not die on the first red assertion.
#
# Run: bash skills/release/tests/test_version_compare.sh
# Exit 0 on all-pass; 1 with a per-assertion diagnostic on failure.

set -uo pipefail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/version-compare.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: version-compare.sh not readable at $SCRIPT" >&2; exit 2; }
# shellcheck source=skills/release/version-compare.sh
source "$SCRIPT"

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

# version_gt a b must exit 0 (a strictly greater than b).
assert_gt() {
  local a="$1" b="$2"
  if version_gt "$a" "$b"; then
    pass "version_gt '${a}' '${b}' -> greater"
  else
    fail "version_gt '${a}' '${b}' expected greater (0), got not-greater"
  fi
}

# version_gt a b must exit non-zero (a NOT strictly greater than b).
assert_not_gt() {
  local a="$1" b="$2"
  if version_gt "$a" "$b"; then
    fail "version_gt '${a}' '${b}' expected NOT greater, got greater"
  else
    pass "version_gt '${a}' '${b}' -> not greater"
  fi
}

echo "== version-compare.sh tests =="

# Numeric, not lexical: 0.3.119 outranks 0.3.20 (lexically '119' < '20').
assert_gt     "0.3.119" "0.3.20"
assert_not_gt "0.3.20"  "0.3.119"

# Ordinary advances across each component.
assert_gt "0.3.10" "0.3.9"
assert_gt "0.4.0"  "0.3.99"
assert_gt "1.0.0"  "0.9.9"

# Equality is NOT strictly greater — a no-op publish must not read as an advance.
assert_not_gt "0.3.31" "0.3.31"
# Downgrade is never greater.
assert_not_gt "0.3.30" "0.3.31"

# Empty/short components default to 0. An empty baseline is the first-publish
# case: any real version must outrank it, and empty-vs-empty is equal.
assert_gt     "0.3.5" ""        # first publish landed: 0.3.5 > 0.0.0
assert_not_gt ""      ""        # both empty -> equal -> not greater
assert_not_gt ""      "0.0.1"   # empty (0.0.0) is not greater than 0.0.1
assert_gt     "1"     "0.9.9"   # missing minor/patch -> 1.0.0 > 0.9.9
assert_gt     "0.3"   "0.2.9"   # missing patch -> 0.3.0 > 0.2.9

# --- direct-execution CLI (the entry-point guard, run as a subprocess so the
#     scoped `set -euo pipefail` and the exit-code contract are exercised) ---
assert_cli() {
  local want="$1" desc="$2"; shift 2
  bash "$SCRIPT" "$@" >/dev/null 2>&1
  local got=$?
  if [[ "$got" == "$want" ]]; then
    pass "CLI ${desc} -> exit ${want}"
  else
    fail "CLI ${desc} expected exit ${want}, got ${got}"
  fi
}
assert_cli 0 "0.3.5 > 0.3.4"          0.3.5 0.3.4
assert_cli 1 "0.3.4 not > 0.3.5"      0.3.4 0.3.5
assert_cli 1 "equal is not greater"   0.3.5 0.3.5
assert_cli 2 "wrong arity (1 arg)"    0.3.5
assert_cli 2 "non-numeric input"      1.2 0.3.4

echo ""
echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ $FAIL_COUNT -eq 0 ]]
