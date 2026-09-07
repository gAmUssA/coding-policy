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
# runs each in its own process (`bash file` / `python3 file`) so one suite's
# `set`/`cd`/traps/interpreter state can't leak into the next. Discovery
# covers BOTH languages deliberately: a `.sh`-only
# glob silently orphaned every Python suite in the repo — they looked
# covered while never executing in CI, including one wired into the publish
# path. Each suite self-drives (a shell harness prints its own summary and
# exits non-zero; a unittest module does the same via its entry-point
# guard), so dispatch is by extension and nothing else.
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
# Interpreters are overridable so a runner with a differently-named binary
# (python3.11, a venv shim) can point at it without editing this file, and
# so the missing-interpreter path is testable without breaking PATH for the
# harness itself.
SH_BIN="${SH_BIN:-bash}"
PY_BIN="${PY_BIN:-python3}"

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
    *.py) interp="$PY_BIN" ;;
    *)
      DISPATCH_FAULT_SUITE="$suite"
      echo "run-tests: no interpreter for suite type: $suite" >&2
      return 1
      ;;
  esac
  # Check the interpreter exists before dispatching. Without this, a runner
  # lacking python3 gives the suite exit 127, which the caller counts as a
  # FAILING TEST rather than the setup error (rc 2) this contract promises —
  # reporting a red suite where the truth is an unprovisioned runner, and
  # sending whoever reads CI to debug a test that never ran.
  if ! command -v "$interp" >/dev/null; then
    DISPATCH_FAULT_SUITE="$suite"
    echo "run-tests: '${interp}' not found on PATH — cannot run ${suite}; install it on this runner (this is a setup error, not a test failure)" >&2
    return 1
  fi
  "$interp" "$suite"
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
    printf '{"suites":0,"passed":0,"failed":0,"failures":[],"error":%s}\n' \
      "$(json_str "no test suites found under ${base}/**/tests/test_*.{sh,py}")"
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
  local failed=() setup_error=""
  for s in "${suites[@]}"; do
    echo "▶ $s" >&2
    local suite_rc=0
    DISPATCH_FAULT_SUITE=""
    run_suite "$s" >&2 || suite_rc=$?
    if [[ -n "$DISPATCH_FAULT_SUITE" ]]; then
      setup_error="$DISPATCH_FAULT_SUITE"
      break
    fi
    if [[ $suite_rc -eq 0 ]]; then
      echo "  ✓ $s" >&2
    else
      echo "  ✗ $s" >&2
      failed+=("$s")
    fi
    echo "" >&2
  done

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
