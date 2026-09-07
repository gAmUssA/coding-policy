#!/usr/bin/env bash
# Outcome-based tests for run-tests.sh — the CI test discoverer/runner.
# Asserts the contract callers depend on:
#   - exit code: 0 all-pass, 1 any-fail, 2 setup error (no suite / bad base)
#   - stdout: a single valid JSON summary
#       {"suites":N,"passed":P,"failed":F,"failures":[...]}
#   - human progress goes to stderr, never stdout (so stdout stays JSON)
# Each case builds a throwaway fixture tree and points the runner at it via
# the base-dir arg so the runner never recurses into the real repo.
#
# Run: bash scripts/tests/test_run_tests.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

set -uo pipefail

RUNNER="$(cd "$(dirname "$0")/.." && pwd)/run-tests.sh"
[[ -x "$RUNNER" ]] || { echo "fatal: run-tests.sh not executable at $RUNNER" >&2; exit 2; }
command -v jq >/dev/null || { echo "fatal: jq required for these tests" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0
pass() { PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $1"; }
fail() { FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $1" >&2; }

make_base() { mktemp -d; }
add_suite() {
  local base="$1" name="$2" exit_code="$3"
  local dir="$base/skills/$name/tests"
  mkdir -p "$dir"
  printf '#!/usr/bin/env bash\nexit %s\n' "$exit_code" > "$dir/test_$name.sh"
}

# A Python suite, shaped like the real ones: self-driving via the
# entry-point guard rules/file-hygiene.md requires. Discovery matched
# `test_*.sh` only, so every Python suite in the repo was orphaned —
# present, passing when run by hand, never executed by CI.
add_py_suite() {
  local base="$1" name="$2" exit_code="$3"
  local dir="$base/skills/$name/tests"
  mkdir -p "$dir"
  printf 'import sys\nif __name__ == "__main__":\n    sys.exit(%s)\n' \
    "$exit_code" > "$dir/test_$name.py"
}

# Runs the runner; sets OUT (stdout), ERR (stderr), CODE (exit).
invoke() {
  local base="$1" errf; errf="$(mktemp)"
  OUT="$("$RUNNER" "$base" 2>"$errf")"; CODE=$?
  ERR="$(cat "$errf")"; rm -f "$errf"
}

echo "run-tests.sh tests"

# --- all suites pass -> exit 0, JSON summary ---
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" beta 0
invoke "$base"
if [[ "$CODE" == 0 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 0 ]] \
  && [[ "$(jq -r '.failures | length' <<<"$OUT")" == 0 ]]; then
  pass "all pass -> exit 0, passed=2 failed=0"
else
  fail "all pass: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- one suite fails -> exit 1, JSON names it in failures ---
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" doomed 1
invoke "$base"
if [[ "$CODE" == 1 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 1 ]] \
  && jq -e '.failures | any(test("test_doomed.sh$"))' <<<"$OUT" >/dev/null; then
  pass "one fail -> exit 1, failures lists the suite"
else
  fail "one fail: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- stdout is pure JSON; progress lives on stderr ---
base="$(make_base)"; add_suite "$base" alpha 0
invoke "$base"
if jq -e . <<<"$OUT" >/dev/null \
  && ! grep -q "▶" <<<"$OUT" \
  && grep -q "▶" <<<"$ERR"; then
  pass "stdout is JSON, progress on stderr"
else
  fail "stream split: out=$OUT err=$ERR"
fi
rm -rf "$base"

# --- path with space + newline still yields valid JSON (finding #145/#146) ---
base="$(make_base)"
weird="$base/skills/we ird"$'\n'"name/tests"
mkdir -p "$weird"
printf '#!/usr/bin/env bash\nexit 1\n' > "$weird/test_weird.sh"
invoke "$base"
if [[ "$CODE" == 1 ]] \
  && jq -e . <<<"$OUT" >/dev/null \
  && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && jq -e '.failures | any(test("test_weird.sh$"))' <<<"$OUT" >/dev/null; then
  pass "control-char path -> still valid JSON"
else
  fail "control-char path: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- no suites found -> exit 2, JSON error, suites=0 ---
base="$(make_base)"
invoke "$base"
if [[ "$CODE" == 2 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 0 ]] \
  && jq -e 'has("error")' <<<"$OUT" >/dev/null; then
  pass "no suites -> exit 2, JSON error"
else
  fail "no suites: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- missing base dir -> exit 2, JSON error ---
invoke "/nonexistent/path/$$"
if [[ "$CODE" == 2 ]] && jq -e 'has("error")' <<<"$OUT" >/dev/null; then
  pass "missing base dir -> exit 2, JSON error"
else
  fail "missing base: code=$CODE out=$OUT"
fi

# --- a later suite failing still fails the run (no early-exit masking) ---
base="$(make_base)"; add_suite "$base" aaa 0; add_suite "$base" zzz 1
invoke "$base"
if [[ "$CODE" == 1 ]] && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]]; then
  pass "later-suite failure not masked -> exit 1"
else
  fail "later fail: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a Python suite is discovered and run at all ---
# Pre-fix this exits 2 ("no test suites found"): the .sh-only glob matched
# nothing, so a tree of passing Python tests read as an empty tree.
base="$(make_base)"; add_py_suite "$base" pyalpha 0
invoke "$base"
if [[ "$CODE" == 0 ]] && [[ "$(jq -r .suites <<<"$OUT")" == 1 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 1 ]]; then
  pass "python-only tree -> discovered, exit 0, suites=1"
else
  fail "python discovery: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a failing Python suite reddens the run ---
# Discovery alone isn't enough: a suite that runs but whose exit code is
# dropped would count as passed and let a real regression ship green.
base="$(make_base)"; add_py_suite "$base" pyalpha 0; add_py_suite "$base" pydoomed 1
invoke "$base"
if [[ "$CODE" == 1 ]] && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && [[ "$(jq -r '.failures[0]' <<<"$OUT")" == *"test_pydoomed.py" ]]; then
  pass "failing python suite -> exit 1, named in failures"
else
  fail "python failure: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- shell and python suites are counted in one run ---
base="$(make_base)"; add_suite "$base" alpha 0; add_py_suite "$base" pybeta 0
invoke "$base"
if [[ "$CODE" == 0 ]] && [[ "$(jq -r .suites <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .passed <<<"$OUT")" == 2 ]]; then
  pass "mixed sh+py tree -> both counted, suites=2"
else
  fail "mixed discovery: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a suite that exits 2 is a FAILING SUITE, not a dispatcher fault ---
# The harnesses in this repo exit 2 on a fatal precondition ("fatal: <x> not
# executable"). An earlier draft used 2 as the dispatcher's own setup-error
# code, which made those suites read as a broken runner and stopped the run
# — hiding a real red suite behind a setup error nobody could act on.
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" fatal2 2
invoke "$base"
if [[ "$CODE" == 1 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r '.failures[0]' <<<"$OUT")" == *"test_fatal2.sh" ]]; then
  pass "suite exiting 2 -> counted as a failure, not a setup error"
else
  fail "suite exit 2: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a python suite exiting 2 is likewise a failure ---
base="$(make_base)"; add_py_suite "$base" pyfatal2 2
invoke "$base"
if [[ "$CODE" == 1 ]] && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]]; then
  pass "python suite exiting 2 -> counted as a failure"
else
  fail "python exit 2: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a test_* file NESTED under tests/ is NOT dispatched as a suite ---
# `find`'s `*` matches `/`, so `-path '*/tests/test_*.sh'` alone would match
# `tests/test_fixtures/helper.sh`. The dirname==*/tests filter drops it.
base="$(make_base)"; add_suite "$base" alpha 0
nested="$base/skills/alpha/tests/test_fixtures"; mkdir -p "$nested"
printf '#!/usr/bin/env bash\nexit 1\n' > "$nested/test_helper.sh"       # would fail the run if dispatched
printf 'import sys\nif __name__=="__main__": sys.exit(1)\n' > "$nested/test_helper.py"
invoke "$base"
if [[ "$CODE" == 0 ]] && [[ "$(jq -r .suites <<<"$OUT")" == 1 ]]; then
  pass "nested test_* under tests/ -> not a suite (only the direct child counts)"
else
  fail "nested exclusion: code=$CODE out=$OUT (expected suites=1, exit 0)"
fi
rm -rf "$base"

# --- a suite exiting 125 is a FAILING SUITE, not a dispatcher fault ---
# An earlier draft used 125 as the dispatcher's private sentinel. Any
# reserved exit code is only a soft invariant: nothing stops a suite from
# returning it, and a real red suite then reports as a broken runner and
# stops the run. The fault now travels by side channel, so EVERY status a
# suite returns is its own verdict. This pins that.
base="$(make_base)"; add_suite "$base" alpha 0; add_suite "$base" sentinel125 125
invoke "$base"
if [[ "$CODE" == 1 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 2 ]] \
  && [[ "$(jq -r .failed <<<"$OUT")" == 1 ]] \
  && jq -e '.failures | any(test("test_sentinel125.sh$"))' <<<"$OUT" >/dev/null; then
  pass "suite exiting 125 -> counted as a failure, not a setup error"
else
  fail "suite exit 125: code=$CODE out=$OUT"
fi
rm -rf "$base"

# --- a missing interpreter is a SETUP error (2), not a test failure (1) ---
# Dispatching to an absent python3 gives the suite exit 127. Counting that
# as a failing suite reports red tests for a runner that never ran them,
# sending whoever reads CI to debug a test that never executed.
base="$(make_base)"; add_py_suite "$base" pyalpha 0
errf="$(mktemp)"
# Override PY_BIN rather than emptying PATH — the harness needs bash, find,
# and mktemp on PATH to run at all, so nuking it tests the shebang, not the
# dispatcher.
OUT="$(PY_BIN=definitely-not-a-real-python3 "$RUNNER" "$base" 2>"$errf")"; CODE=$?
ERR="$(cat "$errf")"; rm -f "$errf"
if [[ "$CODE" == 2 ]] \
  && [[ "$(jq -r .suites <<<"$OUT")" == 0 ]] \
  && [[ "$(jq -r '.error' <<<"$OUT")" == *"interpreter missing"* ]]; then
  pass "missing interpreter -> exit 2 (setup error), not exit 1"
else
  fail "missing interpreter: code=$CODE out=$OUT err=$ERR"
fi
rm -rf "$base"

echo ""
echo "run-tests.sh: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
[[ $FAIL_COUNT -eq 0 ]] || exit 1
