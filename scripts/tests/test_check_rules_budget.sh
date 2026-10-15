#!/usr/bin/env bash
# Outcome-based tests for check-rules-budget.sh.
#
# Each case builds a fixture rules/ tree of known byte sizes and runs the gate
# against it with a small RULES_BUDGET_BYTES override.
#
# Covers:
#   1. total under budget          -> exit 0, within_budget true, exact total
#   2. total equal to budget       -> exit 0 (the budget is inclusive)
#   3. total over budget           -> exit 1, stderr names the largest file
#   4. files listed largest first  -> order asserted
#   5. directory with no *.md      -> exit 2, empty stdout
#   6. missing directory           -> exit 2
#   7. non-integer budget override -> exit 2
#   8. the repo's own rules/       -> exit 0 at the script's own budget
#
# Run: bash scripts/tests/test_check_rules_budget.sh
set -uo pipefail

# The runner's per-suite token binds this suite's summary line to this
# suite (scripts/run-tests.sh header). Unset it so a harness run from
# here prints an empty token and never stands in for this one.
SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/check-rules-budget.sh"
[[ -f "$SCRIPT" && -r "$SCRIPT" ]] || { echo "fatal: script not readable at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

# bytes <file> <n>: write exactly n bytes.
bytes() {
  head -c "$2" /dev/zero | tr '\0' 'x' > "$1" || { echo "fatal: cannot write $1" >&2; exit 2; }
}

# fixture <dir>: a.md 300 bytes, b.md 500 bytes, c.md 200 bytes, notes.txt 9999 (ignored).
fixture() {
  mkdir -p "$1" || { echo "fatal: mkdir $1" >&2; exit 2; }
  bytes "$1/a.md" 300; bytes "$1/b.md" 500; bytes "$1/c.md" 200; bytes "$1/notes.txt" 9999
}

# run <budget|""> <dir> -> OUT, ERR, RC
run() {
  if [[ -n "$1" ]]; then
    OUT="$(RULES_BUDGET_BYTES="$1" bash "$SCRIPT" "$2" 2>"$TMP/err")"; RC=$?
  else
    OUT="$(bash "$SCRIPT" ${2:+"$2"} 2>"$TMP/err")"; RC=$?
  fi
  ERR="$(cat "$TMP/err")"
}

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT
  fixture "$TMP/rules"

  # 1. under budget
  run 2000 "$TMP/rules"
  if [[ $RC -eq 0 ]] && jq -e '.within_budget == true and .total_bytes == 1000 and .budget_bytes == 2000' <<<"$OUT" >/dev/null; then pass
  else fail "under budget: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. equal to budget
  run 1000 "$TMP/rules"
  if [[ $RC -eq 0 ]] && jq -e '.within_budget == true' <<<"$OUT" >/dev/null; then pass
  else fail "equal budget: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. over budget
  run 999 "$TMP/rules"
  if [[ $RC -eq 1 ]] && jq -e '.within_budget == false and .total_bytes == 1000' <<<"$OUT" >/dev/null \
     && grep -q 'over the 999-byte budget by 1' <<<"$ERR" && grep -q 'b.md (500)' <<<"$ERR"; then pass
  else fail "over budget: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 4. largest first, *.md only
  run 2000 "$TMP/rules"
  if jq -e '[.files[].path] == ["b.md","a.md","c.md"]' <<<"$OUT" >/dev/null; then pass
  else fail "order: OUT=$OUT"; fi

  # 5. no rule files
  mkdir -p "$TMP/empty" || { echo "fatal: cannot create $TMP/empty — check that \$TMPDIR is writable" >&2; exit 2; }
  bytes "$TMP/empty/x.txt" 10
  run 2000 "$TMP/empty"
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q 'no \*.md rule files' <<<"$ERR"; then pass
  else fail "no files: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 6. missing directory
  run 2000 "$TMP/nope"
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q 'not found' <<<"$ERR"; then pass
  else fail "missing dir: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 7. bad override
  run 12abc "$TMP/rules"
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q 'positive integer' <<<"$ERR"; then pass
  else fail "bad override: RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 8. the repo's own rules at the script's budget
  run "" ""
  if [[ $RC -eq 0 ]] && jq -e '.within_budget == true and .budget_bytes == 95000' <<<"$OUT" >/dev/null; then pass
  else fail "repo rules: RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
