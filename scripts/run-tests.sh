#!/usr/bin/env bash
# Discover and run every module's unit-test suite, in one pass.
#
# rules/testing-standards.md mandates that every module ship tests, but
# nothing executed them: the publish pipeline ran lint + skill-review +
# publish, so a red suite on `main` stayed invisible and a regression
# could ship green. This runner is the missing enforcement — CI calls it
# on every PR (tests.yml) and again before publish (publish.yml), so a
# failing suite blocks the merge and the release.
#
# Discovers <base>/**/tests/test_*.sh and <base>/**/tests/test_*.py, and
# runs each in its own process (`bash file` / `uv run ... file`) so one suite's
# `set`/`cd`/traps/interpreter state can't leak into the next. Discovery
# covers BOTH languages deliberately: a `.sh`-only
# glob silently orphaned every Python suite in the repo — they looked
# covered while never executing in CI, including one wired into the publish
# path. Each suite self-drives (a shell harness prints its own summary and
# exits non-zero; a unittest module does the same via its entry-point
# guard), so dispatch is by extension and nothing else.
#
# A shell harness's exit code alone cannot prove it ran anything: each one
# exits on its failure count, so a harness whose cases were all removed or
# never reached exits 0 (#26). Every shell harness therefore prints its
# counts as a summary line, and a shell suite exiting 0 passes only when it
# printed a summary carrying its own token, reporting at least one pass and
# zero failures:
#   test-summary: passed=<N> failed=<M> token=<TEST_SUMMARY_TOKEN>
# The runner exports a fresh TEST_SUMMARY_TOKEN for each suite. A harness
# saves it and unsets it before running anything, so a harness it runs prints
# an empty token and a nested runner hands out tokens of its own. Neither
# kind of nested summary can stand in for the suite's own (#31), wherever it
# sits in the output. A Python suite needs no line of its own: a unittest
# suite exiting 0 passes only when unittest's final "Ran <N> test(s)" line
# reports N > 0 (Python 3.11 exits 0 on zero tests).
#
# Output contract (rules/script-delegation.md — structured stdout):
#   stdout: one JSON object, the run summary —
#     {"suites": N, "passed": P, "failed": F, "failures": ["<path>", ...]}
#     On a setup error an "error" string field is added and suites=0.
#   stderr: human-readable per-suite progress and each suite's own output.
#
# Usage: scripts/run-tests.sh [base-dir]
#   base-dir   Directory to search for suites. Defaults to the repo root
#              (the script's parent's parent). The optional arg exists so
#              the runner's own test can point it at a fixture tree.
# Exit:  0 if every suite passes, 1 if any suite fails, 2 on setup error
#        (base dir missing, or no suite found).

set -euo pipefail

# Dispatch a suite to its interpreter by extension. Both suite kinds are
# self-driving — a shell harness counts its own failures and exits
# non-zero; a unittest module does the same through the entry-point guard
# `rules/file-hygiene.md` requires — so the runner only picks the
# interpreter and reads the exit code.
#
# Python suites run through uv (rules/dependency-management.md Python
# Execution): `uv run --no-project --no-python-downloads --python
# "$PY_REQUIRES"` selects an existing interpreter meeting the stated
# requirement and never downloads one. A missing uv is a setup error, never
# a fallback to ambient python3.
#
# Binaries are overridable so a runner with a differently-named binary can
# point at it without editing this file, and so the missing-interpreter path
# is testable without breaking PATH for the harness itself.
SH_BIN="${SH_BIN:-bash}"
UV_BIN="${UV_BIN:-uv}"
PY_REQUIRES=">=3.11"

# A dispatcher fault must be distinguishable from a suite's own exit code.
# No exit code can do that job: 2 collides with the harnesses here (they
# exit 2 on a fatal precondition), and any reserved sentinel — 125 included
# — is only a soft invariant, since nothing stops a suite from returning it
# and being misreported as a broken runner. So the DISPATCH FAULT does not
# travel by exit code: run_suite sets DISPATCH_FAULT_SUITE for a dispatch
# fault (unknown suite type, missing interpreter). The exit code run_suite
# returns is still the suite's own verdict on the happy path — the caller
# reads it — but it is only trusted as a verdict when the side channel is
# empty.
DISPATCH_FAULT_SUITE=""

run_suite() {
  local suite="$1" interp
  case "$suite" in
    *.sh) interp="$SH_BIN" ;;
    *.py) interp="$UV_BIN" ;;
    *)
      DISPATCH_FAULT_SUITE="$suite"
      echo "run-tests: no interpreter for suite type: $suite" >&2
      return 1
      ;;
  esac
  # Check the interpreter exists before dispatching. Without this, a runner
  # lacking uv gives the suite exit 127, which the caller counts as a
  # FAILING TEST rather than the setup error (rc 2) this contract promises —
  # reporting a red suite where the truth is an unprovisioned runner, and
  # sending whoever reads CI to debug a test that never ran.
  if ! command -v "$interp" >/dev/null; then
    DISPATCH_FAULT_SUITE="$suite"
    echo "run-tests: '${interp}' not found on PATH — cannot run ${suite}; install it on this runner (this is a setup error, not a test failure)" >&2
    return 1
  fi
  # No argument array: an empty "${arr[@]}" is an unbound-variable error
  # under `set -u` on bash 3.2 (stock macOS).
  case "$suite" in
    *.py) "$interp" run --no-project --no-python-downloads --python "$PY_REQUIRES" "$suite" ;;
    *) "$interp" "$suite" ;;
  esac
}

# Judge a shell suite that exited 0 by the summary line carrying its token
# (see header). Prints the reason on stdout when the suite fails the
# contract; prints nothing when it passes. Returns 1 on a contract failure.
SUMMARY_RE='^test-summary: passed=([0-9]+) failed=([0-9]+) token=(.*)$'
summary_verdict() {
  local output="$1" token="$2" line passed="" failed=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ $SUMMARY_RE ]] && [[ "${BASH_REMATCH[3]}" == "$token" ]]; then
      passed="${BASH_REMATCH[1]}" failed="${BASH_REMATCH[2]}"
    fi
  done < "$output"
  if [[ -z "$passed" ]]; then
    echo "exited 0 without a test-summary line carrying its token — start the harness with 'SUMMARY_TOKEN=\"\${TEST_SUMMARY_TOKEN:-}\"; unset TEST_SUMMARY_TOKEN' and end it with 'echo \"test-summary: passed=<N> failed=<M> token=\${SUMMARY_TOKEN}\"'"
    return 1
  fi
  if (( failed > 0 )); then
    echo "exited 0 but reported ${failed} failed"
    return 1
  fi
  if (( passed == 0 )); then
    echo "exited 0 but ran zero cases"
    return 1
  fi
}

# Judge a Python suite that exited 0 by unittest's own final
# "Ran <N> test(s) in ..." line. Prints the reason on stdout when the suite
# fails the contract; prints nothing when it passes. Returns 1 on a contract
# failure. The LAST such line is unittest's own: a nested run's output
# printed earlier never stands in for it.
RAN_RE='^Ran ([0-9]+) tests? in '
python_verdict() {
  local output="$1" line ran=""
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ $RAN_RE ]]; then
      ran="${BASH_REMATCH[1]}"
    fi
  done < "$output"
  if [[ -z "$ran" ]]; then
    echo "exited 0 without unittest's 'Ran <N> tests' line — end the suite with 'unittest.main()' under its entry-point guard"
    return 1
  fi
  if (( ran == 0 )); then
    echo "exited 0 but ran zero tests"
    return 1
  fi
}

# JSON string escaper. A Unix path may contain any byte except NUL and
# '/', so escape the JSON-mandatory characters AND the C0 control set
# (newline/tab/CR/etc.) rather than assuming paths are control-free.
json_str() {
  local s="$1" out="" i ch ord
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  for (( i = 0; i < ${#s}; i++ )); do
    ch="${s:i:1}"
    printf -v ord '%d' "'$ch"
    if (( ord >= 0 && ord < 32 )); then
      case "$ch" in
        $'\n') out+='\n' ;;
        $'\t') out+='\t' ;;
        $'\r') out+='\r' ;;
        $'\b') out+='\b' ;;
        $'\f') out+='\f' ;;
        *) out+="$(printf '\\u%04x' "$ord")" ;;
      esac
    else
      out+="$ch"
    fi
  done
  printf '"%s"' "$out"
}

main() {
  local base="${1:-}"
  if [[ -z "$base" ]]; then
    base="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  if [[ ! -d "$base" ]]; then
    echo "run-tests: base dir not found: $base" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "base dir not found: $base")"
    return 2
  fi

  # NUL-delimited discovery via a temp file. NUL is the only byte a Unix
  # path can't contain, so `-print0`/`sort -z`/`read -d ''` survives paths
  # with spaces or newlines (a newline-delimited pipe would split them).
  # A temp file (not process substitution) keeps `find`'s exit observable:
  # `set -o pipefail` propagates a `find` failure through `| sort -z` to
  # the redirection, and the `if !` catches it — a command substitution
  # can't be used because bash strips NUL bytes from its output.
  local tmplist; tmplist="$(mktemp)"
  # `-path '*/tests/test_*.sh'` narrows to a tests/ dir, but `find`'s `*`
  # matches `/` too, so it still matches a NESTED file like
  # `tests/test_fixtures/helper.sh`. The direct-child requirement can't be
  # expressed portably in `-path`, so discovery casts wide here and the
  # collection loop below enforces `dirname == */tests`.
  if ! find "$base" -type f \( -path '*/tests/test_*.sh' -o -path '*/tests/test_*.py' \) -print0 | sort -z > "$tmplist"; then
    rm -f "$tmplist"
    echo "run-tests: suite discovery (find) failed under $base" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "suite discovery failed under $base")"
    return 2
  fi

  # A suite is a DIRECT child of a `tests/` directory. Drop anything nested
  # deeper (a fixture/helper named test_* under tests/test_fixtures/), which
  # the wide `find` match above still lets through.
  local suites=()
  local s
  while IFS= read -r -d '' s; do
    [[ -n "$s" ]] || continue
    [[ "$(dirname "$s")" == */tests ]] || continue
    suites+=("$s")
  done < "$tmplist"
  rm -f "$tmplist"

  if [[ ${#suites[@]} -eq 0 ]]; then
    echo "run-tests: no test suites found under ${base}/**/tests/test_*.{sh,py}" >&2
    # Bash 3.2 expands braces inside nested command-substitution quotes. Build
    # the message first so json_str receives one argument on stock macOS too.
    local no_suites_error="no test suites found under ${base}/**/tests/test_*.{sh,py}"
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "$no_suites_error")"
    return 2
  fi

  echo "Running ${#suites[@]} test suite(s):" >&2
  echo "" >&2

  # A dispatcher fault (interpreter absent, unknown suite type) is NOT a test
  # verdict. Folding it into `failed` would report "1 suite failed" for a
  # runner that never executed the suite at all — a red CI pointing at
  # innocent tests. Surface it as the contract's setup error (rc 2, suites=0)
  # and stop: subsequent suites would hit the same missing interpreter and
  # bury the cause under noise.
  #
  # Two signals, read in order: the side channel says whether this was a
  # dispatch fault, and only if it wasn't is the exit code trusted as the
  # suite's verdict. That ordering is what lets a suite's own 2 or 125 count
  # as a failure while a dispatch fault (same numeric codes possible) does
  # not — the value never disambiguates them, the side channel does.
  #
  # A suite's output is captured to a file, not piped through `tee`: a pipe
  # would run run_suite in a subshell and lose the side channel.
  local failed=() setup_error="" suite_out reason token n=0
  suite_out="$(mktemp)"
  for s in "${suites[@]}"; do
    echo "▶ $s" >&2
    local suite_rc=0
    DISPATCH_FAULT_SUITE=""
    # A nonce, not a secret: it only has to differ from any token a nested
    # run hands out.
    n=$((n + 1))
    token="$$-${n}-${RANDOM}${RANDOM}"
    TEST_SUMMARY_TOKEN="$token" run_suite "$s" > "$suite_out" 2>&1 || suite_rc=$?
    cat "$suite_out" >&2
    if [[ -n "$DISPATCH_FAULT_SUITE" ]]; then
      setup_error="$DISPATCH_FAULT_SUITE"
      break
    fi
    if [[ $suite_rc -eq 0 && "$s" == *.sh ]] && ! reason="$(summary_verdict "$suite_out" "$token")"; then
      echo "  ✗ $s — $reason" >&2
      failed+=("$s")
    elif [[ $suite_rc -eq 0 && "$s" == *.py ]] && ! reason="$(python_verdict "$suite_out")"; then
      echo "  ✗ $s — $reason" >&2
      failed+=("$s")
    elif [[ $suite_rc -eq 0 ]]; then
      echo "  ✓ $s" >&2
    else
      echo "  ✗ $s" >&2
      failed+=("$s")
    fi
    echo "" >&2
  done
  rm -f "$suite_out"

  if [[ -n "$setup_error" ]]; then
    echo "─────────────────────────────────────────────" >&2
    echo "SETUP ERROR: cannot run ${setup_error} — see stderr above" >&2
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "cannot run ${setup_error}: interpreter missing or unknown suite type")"
    return 2
  fi

  echo "─────────────────────────────────────────────" >&2
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${#failed[@]}/${#suites[@]} suite(s):" >&2
    for s in "${failed[@]}"; do echo "  - $s" >&2; done
  else
    echo "PASSED: all ${#suites[@]} suite(s)." >&2
  fi

  # Structured summary on stdout.
  local failures_json="["
  local i
  for i in "${!failed[@]}"; do
    [[ $i -gt 0 ]] && failures_json+=","
    failures_json+="$(json_str "${failed[$i]}")"
  done
  failures_json+="]"
  printf '{"suites":%d,"passed":%d,"failed":%d,"failures":%s}\n' \
    "${#suites[@]}" "$(( ${#suites[@]} - ${#failed[@]} ))" "${#failed[@]}" "$failures_json"

  [[ ${#failed[@]} -eq 0 ]]
}

[[ "${BASH_SOURCE[0]}" == "${0}" ]] && main "$@"
