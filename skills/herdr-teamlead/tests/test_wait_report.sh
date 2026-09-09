#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/wait-report.sh.
#
# Every case points HERDR_BIN at a fake binary this harness writes, replaying
# an agent state and a pane-probe verdict the case chooses (no live Herdr
# session, no network, no sleep of consequence — the poll interval is driven to
# 0 and the budget to a fixed number of seconds, so the suite is deterministic
# and fast; rules/testing-standards.md Determinism).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Marker + file   -> found true, exit 0, full envelope.
#   2. Marker, no file -> the file is half the signal: found false, exit 1.
#   3. File, no marker -> the marker is the other half: found false, exit 1.
#   4. Late marker     -> a second attempt finds it: found true, exit 0.
#   5. Blocked worker  -> exit 3 immediately, found false, state blocked.
#   6. agent get fails -> exit 2 (tool failure), never a "still working" read.
#   7. Probe fails     -> a non-timeout probe error is exit 2, not "not yet".
#   8. Usage error     -> exit 2 with a usage line.
#   9. Outside Herdr   -> exit 2, empty stdout.
#  10. Relative path   -> refused up front (exit 2), never probed.
#  11. Probe argv      -> options first, PANE_ID last, matched with --match.
#  12. No pane id     -> a payload without one is exit 2, named once.
#  13. No set -u abort -> every case's stderr is checked for "unbound
#                         variable", including the success path, which emits
#                         its JSON and exits 0 before the abort would fire.
#  14. Soft-wrapped     -> rows are never joined or matched independently.
#  14b. Wrap in basename-> a row break inside the basename is exit 4, never found.
#  15. Decoy REPORT     -> another report's line does NOT complete this wait,
#                         including one whose basename ENDS with this one's.
#  16. Confirm read     -> a failing `pane read` is a tool failure (exit 2).
#  17. Blocked flicker  -> one `blocked` read with no dialog keeps waiting.
#  18. Blocked twice    -> two `blocked` reads plus a dialog row is exit 3.
#  19. Blocked, no UI   -> two `blocked` reads without a dialog keep waiting.
#  20. Metachar decoy   -> `.` in the name is literal; `reportXmd` never confirms.
#  21. Unconfirmed idle -> file present + idle twice + no marker = exit 4.
#  21b. Single idle read-> one idle read is not exit 4; the budget ends it.
#  21c. Bad idle-reads  -> a non-integer override is exit 2, not an abort.
#  21d. Zero as `00`    -> refused too; the count is compared in base 10.
#  21e. `02` is two     -> a leading zero is decimal, never octal, downstream.
#  21f. Bad budget      -> the seconds knobs are validated the same way.
#
# Run: bash skills/herdr-teamlead/tests/test_wait_report.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# The fake herdr. `agent get` replays $FAKE_STATUS/$FAKE_PANE (or fails when
# $FAKE_GET_ERR is set, or returns an unreadable payload when $FAKE_GET_BAD is).
# `pane wait-output` answers per $FAKE_MARKER: found | timeout | error |
# late (timeout on the first call, found afterwards, counted in $FAKE_COUNTER).
mk_fake_herdr() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake herdr at $1"
#!/usr/bin/env bash
set -uo pipefail
if [[ -n "${FAKE_CALLS:-}" ]]; then
  printf '%s\n' "$*" >> "$FAKE_CALLS" || exit 2
fi
case "${1:-} ${2:-}" in
  "agent get")
    if [[ -n "${FAKE_GET_ERR:-}" ]]; then
      printf '{"error":{"code":"agent_not_found","message":"no such agent"}}\n' >&2
      exit 1
    fi
    if [[ -n "${FAKE_GET_BAD:-}" ]]; then
      printf '{"id":"cli:agent:get","result":{"type":"agent_info"}}\n'
      exit 0
    fi
    if [[ -n "${FAKE_GET_NO_PANE:-}" ]]; then
      printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","name":"%s"}}}\n' \
        "${FAKE_STATUS:-idle}" "${3:-worker}"
      exit 0
    fi
    status="${FAKE_STATUS:-idle}"
    # A status that changes after the first read, for the flicker cases.
    pane="${FAKE_PANE:-w2:p1}"
    if [[ -n "${FAKE_GET_COUNTER:-}" ]]; then
      n=0
      [[ -r "$FAKE_GET_COUNTER" ]] && read -r n < "$FAKE_GET_COUNTER"
      n=$((n + 1))
      printf '%s\n' "$n" > "$FAKE_GET_COUNTER"
      if (( n >= 2 )); then
        status="${FAKE_STATUS_AFTER:-$status}"
        pane="${FAKE_PANE_AFTER:-$pane}"
      fi
    fi
    printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","pane_id":"%s","name":"%s"}}}\n' \
      "$status" "$pane" "${3:-worker}"
    exit 0
    ;;
  "pane get")
    if [[ -n "${FAKE_CONTEXT_ERR:-}" ]]; then
      printf '{"error":{"code":"pane_not_found"}}\n' >&2
      exit 1
    fi
    if [[ -n "${FAKE_CONTEXT_BAD:-}" ]]; then printf '{}\n'; exit 0; fi
    terminal="${FAKE_TERMINAL:-terminal-1}"
    revision="${FAKE_REVISION:-1}"
    offset="${FAKE_SCROLL:-0}"
    if [[ -n "${FAKE_CONTEXT_COUNTER:-}" ]]; then
      n=0
      [[ -r "$FAKE_CONTEXT_COUNTER" ]] && read -r n < "$FAKE_CONTEXT_COUNTER"
      n=$((n + 1))
      printf '%s\n' "$n" > "$FAKE_CONTEXT_COUNTER" || exit 2
      if (( n >= 2 )); then
        terminal="${FAKE_TERMINAL_AFTER:-$terminal}"
        revision="${FAKE_REVISION_AFTER:-$revision}"
        offset="${FAKE_SCROLL_AFTER:-$offset}"
      fi
    fi
    jq -n --arg pane "${3:?}" --arg terminal "$terminal" --argjson revision "$revision" --argjson offset "$offset" \
      '{result:{pane:{pane_id:$pane, terminal_id:$terminal, revision:$revision,
        agent_status:"idle", scroll:{offset_from_bottom:$offset}}}}'
    exit $?
    ;;
  "pane read")
    text="${FAKE_PANE_TEXT-REPORT: ${FAKE_REPORT_PATH:?fake herdr: report path unset}}"
    if [[ -n "${FAKE_READ_COUNTER:-}" ]]; then
      n=0
      [[ -r "$FAKE_READ_COUNTER" ]] && read -r n < "$FAKE_READ_COUNTER"
      n=$((n + 1))
      printf '%s\n' "$n" > "$FAKE_READ_COUNTER" || exit 2
      if (( n >= 2 )) && [[ -n "${FAKE_PANE_TEXT_AFTER:-}" ]]; then text="$FAKE_PANE_TEXT_AFTER"; fi
      if (( n >= 2 )) && [[ -n "${FAKE_APPEARING_REPORT:-}" ]]; then
        printf 'Completed report\n' > "$FAKE_APPEARING_REPORT" || exit 2
      fi
    fi
    printf '%s\n' "$text"
    exit "${FAKE_PANE_READ_RC:-0}"
    ;;
  "pane wait-output")
    [[ -n "${FAKE_ARGV_FILE:-}" ]] && printf '%s\n' "$*" >> "$FAKE_ARGV_FILE"
    case "${FAKE_MARKER:-timeout}" in
      found) printf '{"id":"cli:pane:wait-output","result":{"type":"pane_output_match"}}\n'; exit 0 ;;
      late)
        n=0
        [[ -r "${FAKE_COUNTER:?fake herdr: FAKE_COUNTER unset}" ]] && read -r n < "$FAKE_COUNTER"
        n=$((n + 1))
        printf '%s\n' "$n" > "$FAKE_COUNTER"
        if (( n >= 2 )); then
          printf '{"id":"cli:pane:wait-output","result":{"type":"pane_output_match"}}\n'
          exit 0
        fi
        printf '{"error":{"code":"timeout","message":"timed out waiting for output match"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
      error)
        printf '{"error":{"code":"pane_not_found","message":"no such pane"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
      *)
        printf '{"error":{"code":"timeout","message":"timed out waiting for output match"},"id":"cli:pane:wait-output"}\n' >&2
        exit 1
        ;;
    esac
    ;;
esac
printf '{"error":{"code":"unsupported","message":"fake herdr: %s"}}\n' "$*" >&2
exit 2
FAKE
  chmod +x "$1" || die "could not chmod the fake herdr at $1"
}

# run <report-path> [extra env...] -> OUT, ERRTEXT, RC
run() {
  local report="$1"; shift
  local -a options=()
  if [[ "${CHECK_ONCE:-0}" == 1 ]]; then options=(--once); fi
  RUN_SEQ=$((RUN_SEQ+1))
  local err="$TMP/stderr.$RUN_SEQ"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    TEAMLEAD_REFUSAL_CONFIRM_SEC=0 \
    FAKE_PANE_TEXT="REPORT: ${report}" \
    "$@" bash "$SCRIPT" ${options[@]+"${options[@]}"} worker "$report" </dev/null 2>"$err")"
  RC=$?
  ERRTEXT="$(cat "$err")"
  assert_no_unbound "$ERRTEXT" "run #${RUN_SEQ}"
}

# A `set -u` abort fires mid-flight, after output has already been emitted, so
# an exit code and a stdout payload can both look correct while the script died
# on its way out. Every case checks for it (a live run hit exactly that: the
# success JSON, exit 0, then `pane: unbound variable`).
assert_no_unbound() { # <stderr-text> <label>
  case "$1" in
    *"unbound variable"*)
      fail "$2: stderr carries a set -u abort: $1" ;;
    *) pass ;;
  esac
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/wait-report.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "wait-report.sh not found/readable at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"

  TMP="$(mktemp -d -t teamlead-wait-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  FAKE="$TMP/herdr"
  mk_fake_herdr "$FAKE"

  local report="$TMP/report.md" missing="$TMP/never-written.md"
  export FAKE_REPORT_PATH="$report"
  printf '# report\n' > "$report" || die "could not write $report"

  FAIL=0; PASS=0; RUN_SEQ=0
  local base=""

  # 1. Both signals present -> found, exit 0, and the envelope carries every
  #    field the contract promises.
  run "$report" FAKE_MARKER=found FAKE_STATUS=done
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg p "$report" '
      (.agent == "worker") and (.found == true) and (.state == "done")
      and (.report_path == $p) and ((.elapsed_seconds | type) == "number")' >/dev/null 2>&1; then
    pass; else fail "marker+file: expected found true and a full envelope, got RC=$RC OUT=$OUT"; fi

  # 2. The marker without the file is not completion — a worker can print the
  #    line and still have written nothing.
  run "$missing" FAKE_MARKER=found FAKE_STATUS=idle
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "idle"' >/dev/null 2>&1; then
    pass; else fail "marker without file: expected found false + exit 1, got RC=$RC OUT=$OUT"; fi

  # 3. The file without the marker is not completion either — a half-written
  #    report from an earlier round looks identical on disk.
  run "$report" FAKE_MARKER=timeout FAKE_STATUS=working
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "working"' >/dev/null 2>&1; then
    pass; else fail "file without marker: expected found false + exit 1, got RC=$RC OUT=$OUT"; fi

  # 4. A marker that only shows up on a later attempt still completes the wait.
  local counter="$TMP/probe-count"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=60 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=late FAKE_COUNTER="$counter" FAKE_STATUS=working \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/stderr.$RUN_SEQ")"; RC=$?
  local probes=0
  [[ -r "$counter" ]] && read -r probes < "$counter"
  if [[ $RC -eq 0 && "$probes" == "2" ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "late marker: expected found true after 2 probes, got RC=$RC probes=$probes OUT=$OUT"; fi

  # 5. A blocked worker is waiting on a human — return at once, do not spend
  #    the budget.
  run "$report" FAKE_MARKER=found FAKE_STATUS=blocked \
    FAKE_PANE_TEXT="Do you want to allow this edit?"
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "blocked"' >/dev/null 2>&1; then
    pass; else fail "blocked: expected exit 3 with state blocked, got RC=$RC OUT=$OUT"; fi

  # 6. A failing `agent get` is a tool failure, never "still working".
  run "$report" FAKE_MARKER=found FAKE_GET_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "agent_not_found"; then
    pass; else fail "agent get failure: expected exit 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 6b. An unreadable `agent get` payload is a tool failure too.
  run "$report" FAKE_MARKER=found FAKE_GET_BAD=1
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "unreadable agent get: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 7. A non-timeout probe error must not read as "the marker is not there yet".
  run "$report" FAKE_MARKER=error
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "pane_not_found"; then
    pass; else fail "probe failure: expected exit 2 surfacing the code, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 8. Usage error -> exit 2 with a usage line.
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" bash "$SCRIPT" worker </dev/null 2>"$TMP/e8")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e8"; then
    pass; else fail "usage: expected exit 2 with a usage line, got RC=$RC OUT=$OUT"; fi

  # 9. Outside Herdr -> refuse, no stdout.
  OUT="$(env -u HERDR_ENV HERDR_BIN="$FAKE" bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e9")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "Herdr" "$TMP/e9"; then
    pass; else fail "outside Herdr: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 10. A relative report path resolves against the caller's cwd, so it is
  #     refused before any probe rather than silently answering about the
  #     wrong file.
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" FAKE_MARKER=found \
    bash "$SCRIPT" worker "reports/worker.md" </dev/null 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "relative" "$TMP/e10"; then
    pass; else fail "relative path: expected exit 2 naming it, got RC=$RC OUT=$OUT"; fi

  # 10b. The refusal names the absolute form to pass instead. The literal
  #      searched for is `$PWD/`, so the pattern stays single-quoted
  #      (shellcheck SC2016 does not apply: no expansion is wanted here).
  # shellcheck disable=SC2016
  if grep -q '\$PWD/' "$TMP/e10"; then
    pass; else fail "relative path: expected an actionable message, got $(cat "$TMP/e10")"; fi

  # 11. herdr's usage line is `pane wait-output [OPTIONS] <--match|--regex>
  #     <PANE_ID>`; the probe must match it, and use --match for a literal.
  local argvfile="$TMP/probe-argv"
  RUN_SEQ=$((RUN_SEQ+1))
  env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_ARGV_FILE="$argvfile" FAKE_PANE="w9:p9" \
    bash "$SCRIPT" worker "$report" </dev/null >/dev/null 2>"$TMP/stderr.$RUN_SEQ"
  local probe_argv=""
  [[ -r "$argvfile" ]] && read -r probe_argv < "$argvfile"
  if [[ "$probe_argv" == *"--match REPORT: "* && "$probe_argv" == *" w9:p9" ]]; then
    pass; else fail "probe argv: expected options first and the pane id last, got '$probe_argv'"; fi
  if [[ "$probe_argv" != *"--regex"* ]]; then
    pass; else fail "probe argv: a literal marker must use --match, got '$probe_argv'"; fi

  # 12. An `agent get` payload with no pane id would otherwise probe the
  #     literal pane "unknown" on every attempt and report a generic herdr
  #     error; name the real cause once instead.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_GET_NO_PANE=1 \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e12")"; RC=$?
  ERRTEXT="$(cat "$TMP/e12")"
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "reported no pane id"; then
    pass; else fail "no pane id: expected exit 2 naming it, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  assert_no_unbound "$ERRTEXT" "no pane id"

  # 13. The success path specifically: stdout is the envelope and stderr is
  #     EMPTY. A `set -u` abort here would arrive after the JSON, where an
  #     exit-code check alone would call the run clean.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e13")"; RC=$?
  if [[ $RC -eq 0 && ! -s "$TMP/e13" ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "clean success: expected exit 0 with empty stderr, got RC=$RC ERR=$(cat "$TMP/e13")"; fi

  # 14. A path split across rows has no provable identity. Even an intact
  #     basename must not certify the current file from a different directory.
  RUN_SEQ=$((RUN_SEQ+1))
  local wrapped
  base="$(basename "$report")"
  wrapped="REPORT: /Users/jbaruch/.worktrees/round-3/reports/
${base}"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="$wrapped" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e14")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "wrapped marker: expected found false, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e14")"; fi
  assert_no_unbound "$(cat "$TMP/e14")" "wrapped marker"

  # 14b. The wrap a per-row check cannot see: the row break lands INSIDE the
  #      basename (`reports/12-` / `developer-fix.md`, observed live on a Codex
  #      pane with a 170-character report path). This is NOT a confirmation --
  #      no text-only join can prove the second row continues the first -- so
  #      with the file present and the worker idle the wait must end in exit 4
  #      and hand the pane to the lead, not sit on the budget and not complete.
  RUN_SEQ=$((RUN_SEQ+1))
  local split_inside
  split_inside="  REPORT: /Users/jbaruch/.worktrees/round-3/reports/${base:0:3}
  ${base:3}"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="$split_inside" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e14b")"; RC=$?
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false and (.reason | test("marker unconfirmed"))' >/dev/null 2>&1; then
    pass; else fail "wrap inside basename: expected exit 4 with a reason, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e14b")"; fi
  assert_no_unbound "$(cat "$TMP/e14b")" "wrap inside basename"

  # 15. A `REPORT: ` line for a DIFFERENT report — the previous round's, or
  #     another worker's — must not complete this wait. The report file exists
  #     the whole time, so the pane text is the only thing separating them.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    FAKE_PANE_TEXT="REPORT: /tmp/other-round/developer-notes.md" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e15")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "decoy marker: expected found false, got RC=$RC OUT=$OUT"; fi

  # 15b. The decoy that a substring test gets wrong: another worker's
  #      `reviewer-report.md` CONTAINS this worker's `report.md`. The basename
  #      has to match as a whole path component, not as a suffix.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle \
    FAKE_PANE_TEXT="REPORT: /tmp/other-round/reviewer-${base}" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e15b")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "suffix decoy: expected found false, got RC=$RC OUT=$OUT"; fi

  # 16. The confirming read failing is a tool failure, not "not yet" — the
  #     marker was seen and the answer is unknown, which must not read as a
  #     worker still working.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_READ_RC=1 FAKE_PANE_TEXT="" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e16")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "could not be confirmed" "$TMP/e16"; then
    pass; else fail "confirm read failure: expected exit 2, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e16")"; fi

  # 17. herdr flickered `blocked` for a single read on a Codex pane running in
  #     Full Access, where a permission prompt resolves itself before anything
  #     can see it — the script reported a dialog that was never on screen,
  #     with elapsed_seconds 0. One `blocked` read is not a blocked worker.
  RUN_SEQ=$((RUN_SEQ+1))
  local counter17="$TMP/get-count-17"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_STATUS_AFTER=working \
    FAKE_GET_COUNTER="$counter17" FAKE_PANE_TEXT="thinking…" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e17")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1 \
     && grep -q "flicker" "$TMP/e17"; then
    pass; else fail "blocked flicker: expected the wait to continue, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e17")"; fi

  # 18. Two `blocked` reads AND a dialog row on the pane is the real thing.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked \
    FAKE_PANE_TEXT="  1. Yes  2. No
  Press enter to continue" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e18")"; RC=$?
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '.state == "blocked" and .found == false' >/dev/null 2>&1; then
    pass; else fail "blocked confirmed: expected exit 3, got RC=$RC OUT=$OUT"; fi

  # 18b. The refusal points at the operator, never at the lead answering it.
  if grep -q "let them answer it" "$TMP/e18"; then
    pass; else fail "blocked confirmed: expected the operator-answers message, got $(cat "$TMP/e18")"; fi

  # 19. Two `blocked` reads with nothing on screen is still not a dialog: a
  #     pane with no marker keeps the wait alive rather than ending the round.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_PANE_TEXT="⠧ working on it" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e19")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "blocked without a dialog: expected the wait to continue, got RC=$RC OUT=$OUT"; fi

  # 20. A regex metacharacter in the name is literal: `report.md` must not
  #     confirm on `reportXmd`. The quoted part of a bash `=~` pattern is
  #     matched as a string; this pins it in case a refactor unquotes it.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=found FAKE_STATUS=idle FAKE_PANE_TEXT="  REPORT: /Users/jbaruch/.worktrees/round-3/reports/${base//./X}" \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e20")"; RC=$?
  if [[ $RC -ne 0 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
    pass; else fail "metachar decoy: expected found false, got RC=$RC OUT=$OUT"; fi

  # 20b. Exact identity and row context matter even with the current file
  #      present. Exercise the public watcher, not just its matching helper.
  local decoy
  for decoy in \
    "REPORT: /example/previous/${base}" \
    "REPORT: ${report}.old" \
    "REPORT: /example/previous/${base}"$'\n'"Current file: ${report}" \
    "REPORT: ${TMP}/"$'\n'"${base}" \
    "> REPORT: ${report}" \
    "- REPORT: ${report}" \
    $'> quoted example\n'"REPORT: ${report}" \
    $'- authored example\n'"REPORT: ${report}" \
    $'-\tauthored example\n'"REPORT: ${report}" \
    $'1.\tauthored example\n'"REPORT: ${report}" \
    "    REPORT: ${report}" \
    "Previous marker was REPORT: ${report}" \
    '`REPORT: '"${report}"'`' \
    $'```text\n'"REPORT: ${report}"$'\n```' \
    $'~~~\n'"REPORT: ${report}"$'\n~~~' \
    $'````\n```\n'"REPORT: ${report}"$'\n````' \
    $'```\n```not-a-close\n'"REPORT: ${report}"$'\n```'; do
    run "$report" FAKE_MARKER=found FAKE_STATUS=done FAKE_PANE_TEXT="$decoy"
    if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1; then
      pass; else fail "unconfirmed identity/context: RC=$RC OUT=$OUT pane=$decoy"; fi
  done

  # A quoted old marker does not suppress a separate current completion line.
  run "$report" FAKE_MARKER=found FAKE_STATUS=working \
    FAKE_PANE_TEXT=$'```\nREPORT: /example/previous/report.md\n```\n'"  REPORT: ${report}"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "independent current marker: RC=$RC OUT=$OUT"; fi

  # Authored list/quote examples inside a closed fence cannot turn the later
  # delivery row into a lazy continuation of that example.
  local fenced_example
  for fenced_example in '- removed' '> quoted example' '1. numbered example'; do
    run "$report" FAKE_MARKER=found FAKE_STATUS=working \
      FAKE_PANE_TEXT=$'```\n'"$fenced_example"$'\n```\n'"REPORT: ${report}"
    if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
      pass; else fail "marker after fenced example: RC=$RC OUT=$OUT example=$fenced_example"; fi
  done

  # Path metacharacters stay literal, with no regex or word-splitting changes.
  local special="$TMP/report [1]+.md"
  printf '# report\n' > "$special" || die "could not write literal path fixture"
  run "$special" FAKE_MARKER=found
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null 2>&1; then
    pass; else fail "literal current path: RC=$RC OUT=$OUT"; fi

  run "$report"$'\nREPORT: /example/previous/report.md' FAKE_MARKER=found
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "multiline report path must be refused: RC=$RC OUT=$OUT"; fi

  # 21. The file is there, the worker reads idle twice, and the marker is never
  #     seen at all (wait-output times out): exit 4 with a reason, well inside
  #     the budget, instead of an hour of silence.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21")"; RC=$?
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false and (.reason | test("marker unconfirmed"))' >/dev/null 2>&1 \
     && grep -q "still unconfirmed after 2 consecutive reads" "$TMP/e21"; then
    pass; else fail "unconfirmed idle: expected exit 4 with a reason, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21")"; fi
  assert_no_unbound "$(cat "$TMP/e21")" "unconfirmed idle"

  # 21b. One idle read with the file present is NOT exit 4: the counter needs
  #      two in a row. With a zero budget the wait ends on exit 1, no reason.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=0 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21b")"; RC=$?
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and (has("reason") | not)' >/dev/null 2>&1; then
    pass; else fail "single idle read: expected exit 1 (budget) with no reason, got RC=$RC OUT=$OUT"; fi

  # 21c. A bad TEAMLEAD_UNCONFIRMED_IDLE_READS is exit 2 with a named cause,
  #      never an arithmetic abort with no JSON.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_UNCONFIRMED_IDLE_READS=soon \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21c")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "TEAMLEAD_UNCONFIRMED_IDLE_READS must be a positive integer" "$TMP/e21c"; then
    pass; else fail "bad idle-reads override: expected exit 2 naming it, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21c")"; fi
  # 21d. `00` is zero, not a positive count: refused the same way.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_UNCONFIRMED_IDLE_READS=00 \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21d")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "must be a positive integer" "$TMP/e21d"; then
    pass; else fail "zero-with-leading-zero override: expected exit 2, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21d")"; fi
  # 21e. A nonzero value with a leading zero (`02`) is decimal 2, not octal:
  #      the exit-4 threshold fires on the second idle read as with the default.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_UNCONFIRMED_IDLE_READS=02 \
    TEAMLEAD_WAIT_INTERVAL_SEC=0 TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_BLOCKED_CONFIRM_SEC=0 \
    FAKE_MARKER=timeout FAKE_STATUS=idle \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21e")"; RC=$?
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null 2>&1 && ! grep -q "octal\|value too great" "$TMP/e21e"; then
    pass; else fail "leading-zero override: expected exit 4 with no arithmetic error, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21e")"; fi
  # 21f. The seconds knobs are validated too: a non-integer budget is exit 2
  #      with a named cause, never an arithmetic abort or a `sleep` error.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" TEAMLEAD_WAIT_BUDGET_SEC=soon \
    bash "$SCRIPT" worker "$report" </dev/null 2>"$TMP/e21f")"; RC=$?
  if [[ $RC -eq 2 && -z "$OUT" ]] && grep -q "TEAMLEAD_WAIT_BUDGET_SEC must be a non-negative integer" "$TMP/e21f"; then
    pass; else fail "bad budget override: expected exit 2 naming it, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e21f")"; fi

  # 22. Confirmed native refusal is a distinct unavailable attempt, not a
  # dialog or an approval. The zero budget exercises precedence, not elapsed
  # production time; counters prove the independent live-state/UI reads.
  local refusal=$'This content can\'t be shown\n\n› Ask Codex to do anything'
  local calls="$TMP/refusal-calls" gets="$TMP/refusal-gets" reads="$TMP/refusal-reads"
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" \
    FAKE_CALLS="$calls" FAKE_GET_COUNTER="$gets" FAKE_READ_COUNTER="$reads"
  if [[ $RC -eq 5 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "idle" and .reason == "terminal_provider_refusal"' >/dev/null; then
    pass; else fail "terminal refusal: RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  if [[ "$(cat "$gets")" == 2 && "$(cat "$reads")" == 2 ]]; then
    pass; else fail "terminal refusal needs independent confirmation reads"; fi
  local write_rc=0
  grep -Eq 'agent (prompt|start|send)|pane (send|run)' "$calls" || write_rc=$?
  if (( write_rc == 1 )); then pass; else fail "diagnosis must never send input or switch a worker"; fi

  # Native empty composer variants remain explicit and conservative.
  local composer_row
  for composer_row in $'❯\n  ? for shortcuts' $'│ ❯           │\n  Shift+Tab:mode  │  Ctrl+.:shortcuts'; do
    run "$missing" FAKE_MARKER=timeout FAKE_STATUS=done FAKE_PANE_TEXT=$'This content can\'t be shown\n'"$composer_row"
    if [[ $RC -eq 5 ]] && printf '%s' "$OUT" | jq -e '.found == false and .state == "done"' >/dev/null; then
      pass; else fail "empty native composer refusal: RC=$RC OUT=$OUT"; fi
  done

  # Quoted/code examples, stale notices, occupied input and working footers
  # do not diagnose the current attempt as a terminal provider refusal.
  local notice_decoy
  for notice_decoy in \
    $'> This content can\'t be shown\n›' \
    $'```\nThis content can\'t be shown\n```\n›' \
    $'    This content can\'t be shown\n›' \
    $'This content can\'t be shown\nNew assignment from the team lead\n›' \
    $'This content can\'t be shown\nA later response completed normally\n›' \
    $'This content can\'t be shown\n› pending input' \
    $'This content can\'t be shown\n│ ❯       │\nShift+Tab:mode  │  Esc:cancel  │  Ctrl+.:shortcuts'; do
    run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$notice_decoy"
    if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.found == false and (has("reason") | not)' >/dev/null; then
      pass; else fail "stale/quoted/nonterminal notice: RC=$RC OUT=$OUT pane=$notice_decoy"; fi
  done
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=working FAKE_PANE_TEXT="$refusal"
  if [[ $RC -eq 1 ]]; then pass; else fail "working worker with old notice: RC=$RC OUT=$OUT"; fi

  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_STATUS_AFTER=working \
    FAKE_GET_COUNTER="$TMP/refusal-flicker" FAKE_PANE_TEXT="$refusal"
  if [[ $RC -eq 1 ]] && printf '%s' "$OUT" | jq -e '.state == "working" and .found == false' >/dev/null; then
    pass; else fail "idle flicker must preserve the later working observation: RC=$RC OUT=$OUT"; fi

  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_AFTER=w9:p9 \
    FAKE_GET_COUNTER="$TMP/refusal-pane-change" FAKE_PANE_TEXT="$refusal"
  if [[ $RC -eq 1 ]]; then pass; else fail "changed pane cannot confirm the old terminal notice"; fi

  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" \
    FAKE_READ_COUNTER="$TMP/refusal-output-change" FAKE_PANE_TEXT_AFTER=$'New activity\n'"$refusal"
  if [[ $RC -eq 1 ]]; then pass; else fail "changing output cannot confirm a current terminal notice"; fi

  run "$report" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" TEAMLEAD_UNCONFIRMED_IDLE_READS=1
  if [[ $RC -eq 4 ]] && printf '%s' "$OUT" | jq -e '.found == false' >/dev/null; then
    pass; else fail "unconfirmed report file must keep its existing outcome: RC=$RC OUT=$OUT"; fi
  run "$report" FAKE_MARKER=found FAKE_STATUS=done FAKE_PANE_TEXT="REPORT: ${report}"$'\n'"$refusal"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null; then
    pass; else fail "genuine file and marker still establish delivery: RC=$RC OUT=$OUT"; fi

  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_READ_RC=1
  if [[ $RC -eq 2 && -z "$OUT" ]]; then pass; else fail "refusal probe tool failure must remain an error"; fi
  run "$missing" FAKE_MARKER=timeout TEAMLEAD_REFUSAL_CONFIRM_SEC=soon
  if [[ $RC -eq 2 && -z "$OUT" && "$ERRTEXT" == *TEAMLEAD_REFUSAL_CONFIRM_SEC* ]]; then
    pass; else fail "invalid refusal confirmation setting must have an actionable error"; fi

  # Scrolled history or absent metadata never proves the current UI ended.
  local offset
  for offset in 1 null; do
    run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" FAKE_SCROLL="$offset"
    if [[ $RC -eq 1 ]]; then pass; else fail "historical/unknown scroll position must keep waiting: RC=$RC"; fi
  done
  local changed_context
  for changed_context in FAKE_TERMINAL_AFTER=terminal-2 FAKE_REVISION_AFTER=2 FAKE_SCROLL_AFTER=1; do
    run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" \
      FAKE_CONTEXT_COUNTER="$TMP/context-$changed_context" "$changed_context"
    if [[ $RC -eq 1 ]]; then pass; else fail "changed terminal context cannot confirm refusal: $changed_context RC=$RC"; fi
  done
  local context_error
  for context_error in FAKE_CONTEXT_ERR=1 FAKE_CONTEXT_BAD=1; do
    run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" "$context_error"
    if [[ $RC -eq 2 && -z "$OUT" ]]; then pass; else fail "unreadable terminal context must be a tool error: $context_error"; fi
  done

  # A report arriving during confirmation cancels refusal; the next poll
  # still requires its actual file plus complete marker before delivery.
  local arriving="$TMP/arriving-report.md"
  run "$arriving" FAKE_MARKER=late FAKE_COUNTER="$TMP/arriving-marker" \
    FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" FAKE_READ_COUNTER="$TMP/arriving-reads" \
    FAKE_PANE_TEXT_AFTER="REPORT: $arriving" FAKE_APPEARING_REPORT="$arriving" TEAMLEAD_WAIT_BUDGET_SEC=600
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null; then
    pass; else fail "delivery during confirmation must cancel refusal: RC=$RC OUT=$OUT"; fi

  mkdir "$TMP/sleep-failure" || die "cannot create sleep failure fixture"
  printf '#!/bin/sh\nexit 1\n' > "$TMP/sleep-failure/sleep" || die "cannot write sleep failure fixture"
  chmod +x "$TMP/sleep-failure/sleep" || die "cannot enable sleep failure fixture"
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal" PATH="$TMP/sleep-failure:$PATH"
  if [[ $RC -eq 2 && -z "$OUT" && "$ERRTEXT" == *'confirmation wait failed'* ]]; then
    pass; else fail "failed confirmation delay must remain a tool error"; fi

  # A fleet checkpoint returns pending after one observation even when the
  # ordinary long wait budget would allow more polls. Its prefix probe is
  # bounded by the checkpoint contract, not the long wait override.
  CHECK_ONCE=1
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=working \
    FAKE_GET_COUNTER="$TMP/once-pending-count" FAKE_CALLS="$TMP/once-calls" \
    TEAMLEAD_WAIT_BUDGET_SEC=600 TEAMLEAD_PROBE_TIMEOUT_MS=9999
  if [[ $RC -eq 1 && "$(cat "$TMP/once-pending-count")" == 1 ]] && \
    printf '%s' "$OUT" | jq -e '.found == false and .reason == "checkpoint_pending"' >/dev/null; then
    pass; else fail "checkpoint must return pending without waiting for future output: RC=$RC OUT=$OUT"; fi
  if grep -F -- '--timeout 1 ' "$TMP/once-calls" >/dev/null; then
    pass; else fail "checkpoint must use the bounded prefix probe"; fi

  run "$report" FAKE_MARKER=found FAKE_STATUS=working
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.found == true' >/dev/null; then
    pass; else fail "checkpoint preserves file-plus-marker delivery independently of Herdr status"; fi
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_PANE_TEXT='Allow this action?'
  if [[ $RC -eq 3 ]]; then pass; else fail "checkpoint still confirms a user dialog"; fi
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=blocked FAKE_PANE_TEXT='Allow this action?' \
    FAKE_PANE_AFTER=w9:p9 FAKE_GET_COUNTER="$TMP/once-dialog-moved"
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "a moved worker cannot confirm the old pane's dialog"; fi
  run "$missing" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_PANE_TEXT="$refusal"
  if [[ $RC -eq 5 ]]; then pass; else fail "checkpoint still confirms terminal provider refusal"; fi

  mkdir "$TMP/no-delay" || die "cannot create checkpoint sleep fixture"
  printf '#!/bin/sh\nexit 0\n' > "$TMP/no-delay/sleep" || die "cannot write checkpoint sleep fixture"
  chmod +x "$TMP/no-delay/sleep" || die "cannot enable checkpoint sleep fixture"
  run "$report" FAKE_MARKER=timeout FAKE_STATUS=idle FAKE_GET_COUNTER="$TMP/once-idle-count" \
    PATH="$TMP/no-delay:$PATH" TEAMLEAD_WAIT_BUDGET_SEC=600
  if [[ $RC -eq 4 && "$(cat "$TMP/once-idle-count")" == 2 ]]; then
    pass; else fail "checkpoint must retain consecutive confirmation for missing delivery markers"; fi
  CHECK_ONCE=0

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
