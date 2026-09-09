#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-standup/standup-ask.sh.
#
# Every case points HERDR_BIN at a fake this harness writes, which records the
# argv it was handed (rules/testing-standards.md — no live Herdr session).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Idle worker    -> prompted, sent true.
#   2. Done worker    -> prompted too; done is ready, not busy.
#   3. Working worker -> exit 3, NOTHING sent (a standup never interrupts).
#   4. Blocked worker -> exit 3, nothing sent.
#   5. Message shape  -> `agent prompt`, never a slash command, and the four
#                        field names plus the report path are in the text.
#   6. Relative path  -> exit 1 before any herdr call.
#  6b. Over-long path -> exit 1; the marker line must fit one pane row.
#  6c. Bad limit      -> a non-integer override is exit 1, not an abort.
#  6d. `0100` is 100  -> a leading zero is decimal, never octal, downstream.
#   7. Outside Herdr  -> exit 1.
#   8. herdr failure  -> exit 2, no verdict.
#   9. Bad payload    -> exit 2.
#
# Run: bash skills/herdr-standup/tests/test_standup_ask.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_fake_herdr() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake herdr"
#!/usr/bin/env bash
set -uo pipefail
[[ -n "${FAKE_ARGV_FILE:-}" ]] && printf '%s\n' "$*" >> "$FAKE_ARGV_FILE"
case "${1:-} ${2:-}" in
  "agent get")
    [[ -n "${FAKE_GET_ERR:-}" ]] && { printf '{"error":{"code":"agent_not_found"}}\n' >&2; exit 1; }
    [[ -n "${FAKE_GET_BAD:-}" ]] && { printf '{"id":"cli:agent:get","result":{}}\n'; exit 0; }
    printf '{"id":"cli:agent:get","result":{"type":"agent_info","agent":{"agent":"claude","agent_status":"%s","pane_id":"w2:p1","name":"%s"}}}\n' \
      "${FAKE_STATUS:-idle}" "${3:-worker}"
    exit 0
    ;;
  "agent prompt")
    [[ -n "${FAKE_PROMPT_ERR:-}" ]] && { printf '{"error":{"code":"agent_blocked"}}\n' >&2; exit 1; }
    printf '{"id":"cli:agent:prompt","result":{"type":"agent_prompt"}}\n'
    exit 0
    ;;
esac
printf '{"error":{"code":"unsupported"}}\n' >&2
exit 2
FAKE
  chmod +x "$1" || die "could not chmod the fake herdr"
}

run() { # [env...] -- runs standup-ask worker <report>
  RUN_SEQ=$((RUN_SEQ+1))
  ARGV="$TMP/argv.$RUN_SEQ"
  : > "$ARGV" || die "could not create $ARGV"
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" FAKE_ARGV_FILE="$ARGV" "$@" \
    bash "$SCRIPT" worker "$REPORT" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  ARGVTEXT="$(cat "$ARGV")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/standup-ask.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "standup-ask.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t standup-ask-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  FAKE="$TMP/herdr"; mk_fake_herdr "$FAKE"
  REPORT="$TMP/reports/worker.md"
  # The temp dir alone is near the production path limit; the limit has its
  # own case (6b) and every other case runs under a limit it cannot hit.
  export STANDUP_REPORT_PATH_MAX_COLS=1000
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. An idle worker is asked.
  run FAKE_STATUS=idle
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.sent == true and .agent == "worker" and .state == "idle"' >/dev/null 2>&1; then
    pass; else fail "idle: expected sent true, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 2. `done` is the same idle state after unseen work — also ready.
  run FAKE_STATUS=done
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.sent == true' >/dev/null 2>&1; then
    pass; else fail "done: expected sent true, got RC=$RC OUT=$OUT"; fi

  # 3. A standup is worth less than somebody's turn.
  run FAKE_STATUS=working
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '.sent == false and .state == "working"' >/dev/null 2>&1 \
     && ! printf '%s' "$ARGVTEXT" | grep -q "agent prompt"; then
    pass; else fail "working: expected exit 3 with nothing sent, got RC=$RC ARGV=$ARGVTEXT"; fi

  # 4. Same for a worker at a dialog.
  run FAKE_STATUS=blocked
  if [[ $RC -eq 3 ]] && ! printf '%s' "$ARGVTEXT" | grep -q "agent prompt"; then
    pass; else fail "blocked: expected exit 3 with nothing sent, got RC=$RC ARGV=$ARGVTEXT"; fi

  # 5. The question goes as a MESSAGE, and carries the contract it asks for.
  run FAKE_STATUS=idle
  if printf '%s' "$ARGVTEXT" | grep -q "agent prompt worker"; then
    pass; else fail "delivery: expected an agent prompt call, got ARGV=$ARGVTEXT"; fi
  if ! printf '%s' "$ARGVTEXT" | grep -qE 'agent prompt worker /|pane send-text'; then
    pass; else fail "delivery: a standup question must not go as a slash command"; fi
  local field ok=1
  for field in "DONE:" "PLAN:" "BLOCKED:" "REPORT:"; do
    printf '%s' "$ARGVTEXT" | grep -q "$field" || ok=0
  done
  if [[ $ok -eq 1 ]] && printf '%s' "$ARGVTEXT" | grep -q "$REPORT"; then
    pass; else fail "prompt text: expected the four fields and the report path, got ARGV=$ARGVTEXT"; fi

  # 6. A relative report path resolves in the WORKER's cwd, not the lead's.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" bash "$SCRIPT" worker "reports/w.md" 2>"$TMP/e6")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "relative" "$TMP/e6"; then
    pass; else fail "relative path: expected exit 1, got RC=$RC"; fi

  # 6b. A report path that would wrap the worker's marker line is refused.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" STANDUP_REPORT_PATH_MAX_COLS=100 bash "$SCRIPT" worker "/very/long/reports/directory/that/keeps/going/and/going/round-3/reports/standup-answer-from-worker.md" 2>"$TMP/e6b")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "limit" "$TMP/e6b"; then
    pass; else fail "long path: expected exit 1 naming the limit, got RC=$RC OUT=$OUT"; fi

  # 6c. A bad limit override is a precondition failure, never an arithmetic abort.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" STANDUP_REPORT_PATH_MAX_COLS=soon bash "$SCRIPT" worker "$REPORT" 2>"$TMP/e6c")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "STANDUP_REPORT_PATH_MAX_COLS must be a positive integer" "$TMP/e6c"; then
    pass; else fail "bad limit override: expected exit 1 naming it, got RC=$RC OUT=$OUT"; fi

  # 6d. A leading-zero override is decimal downstream: `0100` rejects the long
  #     path like `100` does, with no octal reparse.
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env HERDR_ENV=1 HERDR_BIN="$FAKE" STANDUP_REPORT_PATH_MAX_COLS=0100 bash "$SCRIPT" worker "/very/long/reports/directory/that/keeps/going/and/going/round-3/reports/standup-answer-from-worker.md" 2>"$TMP/e6d")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "limit is 100" "$TMP/e6d" && ! grep -q "value too great" "$TMP/e6d"; then
    pass; else fail "leading-zero limit: expected exit 1 naming limit 100, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e6d")"; fi

  # 7. Outside Herdr.
  OUT="$(env -u HERDR_ENV HERDR_BIN="$FAKE" bash "$SCRIPT" worker "$REPORT" 2>"$TMP/e7")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "Herdr" "$TMP/e7"; then
    pass; else fail "outside Herdr: expected exit 1, got RC=$RC"; fi

  # 8. A herdr failure is never a verdict about the worker.
  run FAKE_GET_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "agent_not_found"; then
    pass; else fail "herdr failure: expected exit 2, got RC=$RC OUT=$OUT"; fi

  # 9. An unreadable payload is a tool failure too.
  run FAKE_GET_BAD=1
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "bad payload: expected exit 2, got RC=$RC OUT=$OUT"; fi

  # 9b. A refused prompt (the agent went blocked between the read and the send)
  #     is surfaced, never reported as sent.
  run FAKE_STATUS=idle FAKE_PROMPT_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "agent_blocked"; then
    pass; else fail "prompt refused: expected exit 2, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
