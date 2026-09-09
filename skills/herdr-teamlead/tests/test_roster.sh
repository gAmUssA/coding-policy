#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/roster.sh.
#
# The script shells out to `herdr`, so every case points HERDR_BIN at a fake
# binary this harness writes, replaying a fixture payload built programmatically
# in the test (rules/testing-standards.md Fixtures — no binary fixtures, no
# network, no live Herdr session). Each case writes its own payload file, so the
# cases share no mutable state and run in any order (Independence).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Named workers      -> one entry each, carrying name/kind/pane_id/state.
#   2. Caller excluded    -> a NAMED agent on the caller's pane is left out.
#   3. Unnamed excluded   -> a pane with no name has no dispatch handle.
#   4. Empty roster       -> {"agents": []}, exit 0 (a valid roster).
#   5. Outside Herdr      -> exit 1, empty stdout.
#   6. No caller pane id  -> exit 1, empty stdout.
#   7. herdr absent       -> exit 1, empty stdout.
#   8. herdr error        -> exit 2, empty stdout, the CLI's message surfaced.
#   9. Unreadable payload -> exit 2, empty stdout (never an empty roster).
#
# Run: bash skills/herdr-teamlead/tests/test_roster.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# Write the fake herdr. It replays $FAKE_LIST_FILE for `agent list`, or fails
# like the real CLI (JSON on stderr, exit 1) when $FAKE_LIST_ERR is set.
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

# agent_json <pane> <kind> <status> [name] -> one element of .result.agents
agent_json() {
  local pane="$1" kind="$2" status="$3" name="${4:-}"
  if [[ -n "$name" ]]; then
    printf '{"agent":"%s","agent_status":"%s","pane_id":"%s","name":"%s","cwd":"/tmp"}' \
      "$kind" "$status" "$pane" "$name"
  else
    printf '{"agent":"%s","agent_status":"%s","pane_id":"%s","cwd":"/tmp"}' \
      "$kind" "$status" "$pane"
  fi
}

# write_list <file> <element>... -> a full `herdr agent list` payload
write_list() {
  local file="$1"; shift
  local body="" i
  for i in "$@"; do
    [[ -n "$body" ]] && body+=","
    body+="$i"
  done
  printf '{"id":"cli:agent:list","result":{"type":"agent_list","agents":[%s]}}\n' "$body" > "$file" \
    || die "could not write the list payload at $file"
}

# run <state-file> [extra env...] -> OUT, ERR, RC
run() {
  local list="$1"; shift
  RUN_SEQ=$((RUN_SEQ+1))
  ERR="$TMP/stderr.$RUN_SEQ"
  OUT="$(env HERDR_ENV=1 HERDR_PANE_ID="$CALLER" HERDR_BIN="$FAKE" FAKE_LIST_FILE="$list" \
    "$@" bash "$SCRIPT" </dev/null 2>"$ERR")"
  RC=$?
  ERRTEXT="$(cat "$ERR")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/roster.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "roster.sh not found/readable at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"

  TMP="$(mktemp -d -t teamlead-roster-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  FAKE="$TMP/herdr"
  mk_fake_herdr "$FAKE"
  CALLER="w1:p1"

  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. Three named workers on their own panes, plus the caller's unnamed pane.
  local l1="$TMP/list1.json"
  write_list "$l1" \
    "$(agent_json "w1:p1" claude working)" \
    "$(agent_json "w2:p1" claude working claude)" \
    "$(agent_json "w3:p1" codex idle codex)" \
    "$(agent_json "w4:p1" grok "done" grok)"
  run "$l1"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      (.caller.pane_id == "w1:p1")
      and ((.agents | length) == 3)
      and (.agents[0] == {name:"claude", kind:"claude", pane_id:"w2:p1", state:"working"})
      and (.agents[2] == {name:"grok", kind:"grok", pane_id:"w4:p1", state:"done"})' >/dev/null 2>&1; then
    pass; else fail "named workers: expected 3 entries with full fields, got RC=$RC OUT=$OUT"; fi

  # 2. A NAMED agent on the caller's own pane is excluded — a lead never
  #    dispatches a brief to itself.
  local l2="$TMP/list2.json"
  write_list "$l2" \
    "$(agent_json "w1:p1" claude idle lead)" \
    "$(agent_json "w2:p1" codex idle codex)"
  run "$l2"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      ((.agents | length) == 1) and (.agents[0].name == "codex")' >/dev/null 2>&1; then
    pass; else fail "caller excluded: expected only codex, got RC=$RC OUT=$OUT"; fi

  # 3. An unnamed pane has no stable dispatch handle and is excluded.
  local l3="$TMP/list3.json"
  write_list "$l3" \
    "$(agent_json "w2:p1" claude idle)" \
    "$(agent_json "w3:p1" codex idle codex)"
  run "$l3"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      ((.agents | length) == 1) and (.agents[0].pane_id == "w3:p1")' >/dev/null 2>&1; then
    pass; else fail "unnamed excluded: expected only the named pane, got RC=$RC OUT=$OUT"; fi

  # 4. Nobody but the caller -> an empty array is a valid roster, exit 0.
  local l4="$TMP/list4.json"
  write_list "$l4" "$(agent_json "w1:p1" claude idle lead)"
  run "$l4"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.agents == []' >/dev/null 2>&1; then
    pass; else fail "empty roster: expected agents == [] and exit 0, got RC=$RC OUT=$OUT"; fi

  # 5. Outside Herdr -> refuse with an actionable message, no stdout.
  OUT="$(env -u HERDR_ENV HERDR_PANE_ID="$CALLER" HERDR_BIN="$FAKE" FAKE_LIST_FILE="$l1" \
    bash "$SCRIPT" </dev/null 2>"$TMP/e5")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "Herdr" "$TMP/e5"; then
    pass; else fail "outside Herdr: expected exit 1 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 6. No caller pane id -> refuse; the roster cannot exclude the lead's pane.
  OUT="$(env -u HERDR_PANE_ID HERDR_ENV=1 HERDR_BIN="$FAKE" FAKE_LIST_FILE="$l1" \
    bash "$SCRIPT" </dev/null 2>"$TMP/e6")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "HERDR_PANE_ID" "$TMP/e6"; then
    pass; else fail "no caller pane: expected exit 1 + empty stdout, got RC=$RC OUT=$OUT"; fi

  # 7. herdr absent -> exit 1 naming the binary.
  OUT="$(env HERDR_ENV=1 HERDR_PANE_ID="$CALLER" HERDR_BIN="$TMP/no-such-herdr" \
    bash "$SCRIPT" </dev/null 2>"$TMP/e7")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "no-such-herdr" "$TMP/e7"; then
    pass; else fail "herdr absent: expected exit 1 naming the binary, got RC=$RC OUT=$OUT"; fi

  # 8. herdr fails -> exit 2 (a tool failure), never an empty roster.
  run "$l1" FAKE_LIST_ERR='{"error":{"code":"no_session","message":"no herdr session"}}'
  if [[ $RC -eq 2 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "no_session"; then
    pass; else fail "herdr error: expected exit 2 surfacing the CLI message, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 9. A payload without .result.agents is unreadable, not an empty team.
  local l9="$TMP/list9.json"
  printf '{"id":"cli:agent:list","result":{"type":"agent_list"}}\n' > "$l9" || die "could not write $l9"
  run "$l9"
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "unreadable payload: expected exit 2 + empty stdout, got RC=$RC OUT=$OUT"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
