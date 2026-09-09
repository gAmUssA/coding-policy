#!/usr/bin/env bash
# Outcome-based tests for hooks/herdr-team-status.sh.
#
# The hook reads the live roster through skills/herdr-teamlead/roster.sh, which
# shells out to `herdr`, so every case points HERDR_BIN at a fake binary this
# harness writes, replaying a payload built programmatically in the test
# (rules/testing-standards.md Fixtures — no binary fixtures, no live session).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Team present   -> marker payload naming each worker, kind, and state.
#   2. Only this pane -> silent no-op, exit 0.
#   3. Outside Herdr  -> silent no-op, exit 0 (the common case).
#   4. herdr broken   -> silent on stdout, warning on stderr, exit 0.
#   5. herdr absent   -> silent on stdout, warning on stderr, exit 0.
#
# Run: bash hooks/tests/test_herdr_team_status.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_fake_herdr() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake herdr at $1"
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-} ${2:-}" == "agent list" ]]; then
  if [[ -n "${FAKE_LIST_ERR:-}" ]]; then
    printf '%s\n' "$FAKE_LIST_ERR" >&2
    exit 1
  fi
  cat "${FAKE_LIST_FILE:?fake herdr: FAKE_LIST_FILE unset}"
  exit 0
fi
printf '{"error":{"code":"unsupported","message":"fake herdr: %s"}}\n' "$*" >&2
exit 2
FAKE
  chmod +x "$1" || die "could not chmod the fake herdr at $1"
}

# run <list-file> [extra env...] -> OUT, ERRTEXT, RC
run() {
  local list="$1"; shift
  RUN_SEQ=$((RUN_SEQ+1))
  local err="$TMP/stderr.$RUN_SEQ"
  OUT="$(env HERDR_ENV=1 HERDR_PANE_ID="w1:p1" HERDR_BIN="$FAKE" FAKE_LIST_FILE="$list" \
    "$@" bash "$SCRIPT" </dev/null 2>"$err")"
  RC=$?
  ERRTEXT="$(cat "$err")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/herdr-team-status.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "hook not found/readable at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"

  TMP="$(mktemp -d -t herdr-team-status-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  FAKE="$TMP/herdr"
  mk_fake_herdr "$FAKE"

  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. Two named workers besides this pane -> one marker line naming both.
  local full="$TMP/full.json"
  cat > "$full" <<'JSON' || die "could not write $full"
{"id":"cli:agent:list","result":{"type":"agent_list","agents":[
  {"agent":"claude","agent_status":"idle","pane_id":"w1:p1"},
  {"agent":"codex","agent_status":"working","pane_id":"w3:p1","name":"codex"},
  {"agent":"grok","agent_status":"idle","pane_id":"w4:p1","name":"grok"}
]}}
JSON
  run "$full"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext
      | test("^Session-start status — herdr team: ")
      and test("codex \\(codex\\) working")
      and test("grok \\(grok\\) idle")' >/dev/null 2>&1; then
    pass; else fail "team present: expected a marker payload naming both workers, got RC=$RC OUT=$OUT"; fi

  # 2. Nobody named but this pane -> nothing worth reporting.
  local solo="$TMP/solo.json"
  cat > "$solo" <<'JSON' || die "could not write $solo"
{"id":"cli:agent:list","result":{"type":"agent_list","agents":[
  {"agent":"claude","agent_status":"idle","pane_id":"w1:p1","name":"lead"}
]}}
JSON
  run "$solo"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then
    pass; else fail "solo: expected a silent exit 0, got RC=$RC OUT=$OUT"; fi

  # 3. Outside Herdr -> silent, and no stderr noise: this is most sessions.
  OUT="$(env -u HERDR_ENV HERDR_BIN="$FAKE" FAKE_LIST_FILE="$full" \
    bash "$SCRIPT" </dev/null 2>"$TMP/e3")"; RC=$?
  if [[ $RC -eq 0 && -z "$OUT" && ! -s "$TMP/e3" ]]; then
    pass; else fail "outside Herdr: expected a silent exit 0, got RC=$RC OUT=$OUT ERR=$(cat "$TMP/e3")"; fi

  # 4. A failing herdr never blocks the session; it warns and no-ops.
  run "$full" FAKE_LIST_ERR='{"error":{"code":"no_session","message":"no herdr session"}}'
  if [[ $RC -eq 0 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "roster read failed"; then
    pass; else fail "herdr error: expected exit 0 + warning, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 5. herdr absent inside a Herdr session -> warn, never block.
  OUT="$(env HERDR_ENV=1 HERDR_PANE_ID="w1:p1" HERDR_BIN="$TMP/no-such-herdr" \
    bash "$SCRIPT" </dev/null 2>"$TMP/e5")"; RC=$?
  if [[ $RC -eq 0 && -z "$OUT" ]] && grep -q "roster read failed" "$TMP/e5"; then
    pass; else fail "herdr absent: expected exit 0 + warning, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
