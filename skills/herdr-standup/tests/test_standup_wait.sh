#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-standup/standup-wait.sh.
#
# The wrapper's whole job is to run the sibling skill's wait-report.sh with the
# standup budget. Each case copies the wrapper into a temp layout next to a
# FAKE wait-report.sh that records the budget and argv it received and exits
# with a scripted code (rules/testing-standards.md — no live Herdr session).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Default budget  -> wait-report.sh sees TEAMLEAD_WAIT_BUDGET_SEC=180.
#   2. Argv forwarded  -> both arguments reach wait-report.sh verbatim.
#   3. Stdout is wait-report.sh's, untouched.
#   4. Exit code       -> 1 and 3 from wait-report.sh come back as 1 and 3.
#   5. Override        -> STANDUP_WAIT_BUDGET_SEC=7 reaches wait-report.sh as 7.
#   6. Bad override    -> exit 2 before wait-report.sh runs.
#   7. Missing sibling -> exit 2 with the install hint, nothing run.
#
# Run: bash skills/herdr-standup/tests/test_standup_wait.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_layout() { # <root> — wrapper copy plus a fake sibling wait-report.sh
  mkdir -p "$1/herdr-standup" "$1/herdr-teamlead" || die "could not create the layout under $1"
  cp "$SCRIPT" "$1/herdr-standup/standup-wait.sh" || die "could not copy the wrapper"
  cat > "$1/herdr-teamlead/wait-report.sh" <<'FAKE' || die "could not write the fake wait-report.sh"
#!/usr/bin/env bash
set -uo pipefail
printf 'budget=%s\nargv=%s\n' "${TEAMLEAD_WAIT_BUDGET_SEC:-unset}" "$*" >> "$FAKE_CALL_FILE"
printf '{"agent":"%s","state":"idle","report_path":"%s","found":true,"elapsed_seconds":0}\n' "${1:-}" "${2:-}"
exit "${FAKE_RC:-0}"
FAKE
  chmod +x "$1/herdr-teamlead/wait-report.sh" || die "could not chmod the fake"
}

run() { # [env...] — runs the wrapper in $LAYOUT with the standard argv
  RUN_SEQ=$((RUN_SEQ+1))
  CALLS="$TMP/calls.$RUN_SEQ"
  : > "$CALLS" || die "could not create $CALLS"
  OUT="$(env FAKE_CALL_FILE="$CALLS" "$@" \
    bash "$LAYOUT/herdr-standup/standup-wait.sh" grok /w/reports/standup-grok.md 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  CALLTEXT="$(cat "$CALLS")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/standup-wait.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "standup-wait.sh not found at $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  trap cleanup EXIT
  PASS=0; FAIL=0; RUN_SEQ=0
  LAYOUT="$TMP/skills"
  mk_layout "$LAYOUT"

  echo "▶ standup-wait.sh" >&2

  # 1. default budget
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$CALLTEXT" | grep -qx 'budget=180'; then
    pass; else fail "default budget: expected budget=180 and exit 0, got RC=$RC CALLS=$CALLTEXT ERR=$ERRTEXT"; fi

  # 2. argv forwarded verbatim
  if printf '%s' "$CALLTEXT" | grep -qx 'argv=grok /w/reports/standup-grok.md'; then
    pass; else fail "argv: expected both arguments forwarded, got CALLS=$CALLTEXT"; fi

  # 3. stdout is wait-report.sh's JSON, untouched
  if [[ "$OUT" == '{"agent":"grok","state":"idle","report_path":"/w/reports/standup-grok.md","found":true,"elapsed_seconds":0}' ]]; then
    pass; else fail "stdout: expected the fake's JSON verbatim, got $OUT"; fi

  # 4. exit codes propagate
  run FAKE_RC=1
  if [[ $RC -eq 1 ]]; then pass; else fail "exit 1 (budget exhausted) should propagate, got RC=$RC"; fi
  run FAKE_RC=3
  if [[ $RC -eq 3 ]]; then pass; else fail "exit 3 (blocked) should propagate, got RC=$RC"; fi

  # 5. override reaches wait-report.sh
  run STANDUP_WAIT_BUDGET_SEC=7
  if [[ $RC -eq 0 ]] && printf '%s' "$CALLTEXT" | grep -qx 'budget=7'; then
    pass; else fail "override: expected budget=7, got RC=$RC CALLS=$CALLTEXT"; fi

  # 6. a non-integer override is refused before anything runs
  run STANDUP_WAIT_BUDGET_SEC=soon
  if [[ $RC -eq 2 && -z "$CALLTEXT" && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'whole number'; then
    pass; else fail "bad override: expected exit 2, nothing run, a named diagnostic; got RC=$RC CALLS=$CALLTEXT OUT=$OUT ERR=$ERRTEXT"; fi

  # 7. missing sibling skill is named, nothing runs
  LAYOUT="$TMP/alone"
  mkdir -p "$LAYOUT/herdr-standup" || die "could not create the lone layout"
  cp "$SCRIPT" "$LAYOUT/herdr-standup/standup-wait.sh" || die "could not copy the wrapper (lone)"
  run
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q 'tessl install'; then
    pass; else fail "missing sibling: expected exit 2 and the install hint, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
