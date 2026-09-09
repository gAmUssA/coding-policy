#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/label-workspaces.sh.
#
# The fake herdr records every argv it is handed, so the assertions are about
# what was sent — including what was NOT sent on a re-run (rules/testing-
# standards.md; no live Herdr session).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Labels the lot   -> lead workspace, each worker's workspace and pane.
#   2. Pane label shape -> the kind alone; dispatch adds the role.
#   3. Idempotent       -> a workspace already named is `unchanged`, unsent.
#   4. Overrides        -> an explicit <agent>=<workspace-id> wins.
#   5. Caller excluded  -> the lead's own pane is never relabelled as a worker.
#   6. Rename failure   -> exit 3, the JSON names it, the run continues.
#   7. Roster failure   -> exit 2.
#   8. Usage / env      -> exit 1.
#   9. Workspace list   -> fetched once and reused for every target.
#
# Run: bash skills/herdr-teamlead/tests/test_label_workspaces.sh
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
  "agent list")
    [[ -n "${FAKE_LIST_ERR:-}" ]] && { printf '{"error":{"code":"no_session"}}\n' >&2; exit 1; }
    cat "${FAKE_LIST_FILE:?FAKE_LIST_FILE unset}"
    exit 0
    ;;
  "workspace list")
    [[ -n "${FAKE_WS_ERR:-}" ]] && { printf '{"error":{"code":"no_session"}}\n' >&2; exit 1; }
    [[ -n "${FAKE_WS_BAD:-}" ]] && { printf '{"result":{}}\n'; exit 0; }
    [[ -n "${FAKE_WS_BAD_MEMBER:-}" ]] && { printf '{"result":{"workspaces":[42]}}\n'; exit 0; }
    cat "${FAKE_WS_FILE:?FAKE_WS_FILE unset}"
    exit 0
    ;;
  "workspace rename"|"pane rename")
    if [[ -n "${FAKE_RENAME_ERR:-}" && "$*" == *"${FAKE_RENAME_ERR}"* ]]; then
      printf '{"error":{"code":"not_found"}}\n' >&2
      exit 1
    fi
    printf '{"id":"cli:rename","result":{"ok":true}}\n'
    exit 0
    ;;
esac
printf '{"error":{"code":"unsupported"}}\n' >&2
exit 2
FAKE
  chmod +x "$1" || die "could not chmod the fake herdr"
}

run() { # [env...] -- then the script args come from ARGS
  RUN_SEQ=$((RUN_SEQ+1))
  ARGV="$TMP/argv.$RUN_SEQ"
  : > "$ARGV" || die "could not create $ARGV"
  OUT="$(env HERDR_ENV=1 HERDR_WORKSPACE_ID=w1 HERDR_PANE_ID=w1:p1 HERDR_BIN="$FAKE" \
    FAKE_ARGV_FILE="$ARGV" FAKE_LIST_FILE="$LIST" FAKE_WS_FILE="$WS" "$@" \
    bash "$SCRIPT" "${ARGS[@]}" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  ARGVTEXT="$(cat "$ARGV")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/label-workspaces.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "label-workspaces.sh not found at $SCRIPT"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t label-ws-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  FAKE="$TMP/herdr"; mk_fake_herdr "$FAKE"

  LIST="$TMP/list.json"
  cat > "$LIST" <<'JSON' || die "could not write the roster"
{"id":"cli:agent:list","result":{"type":"agent_list","agents":[
  {"agent":"claude","agent_status":"idle","pane_id":"w1:p1","workspace_id":"w1"},
  {"agent":"codex","agent_status":"idle","pane_id":"w3:p1","workspace_id":"w3","name":"codex"},
  {"agent":"grok","agent_status":"working","pane_id":"w4:p1","workspace_id":"w4","name":"grok"}
]}}
JSON
  WS="$TMP/ws.json"
  printf '{"id":"cli:workspace:list","result":{"workspaces":[{"workspace_id":"w1","name":""},{"workspace_id":"w3","name":""},{"workspace_id":"w4","name":""}]}}\n' > "$WS" \
    || die "could not write the workspace list"

  FAIL=0; PASS=0; RUN_SEQ=0
  local ARGS

  # 1. Everything gets a name.
  ARGS=(lead)
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      .lead.state == "renamed" and .lead.workspace_id == "w1"
      and ((.agents | length) == 2)
      and (.agents[0].workspace == "renamed") and (.agents[0].pane == "renamed")' >/dev/null 2>&1; then
    pass; else fail "labels: got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  if printf '%s' "$ARGVTEXT" | grep -q "workspace rename w1 lead" \
     && printf '%s' "$ARGVTEXT" | grep -q "workspace rename w3 codex"; then
    pass; else fail "labels: expected the workspace renames, got ARGV=$ARGVTEXT"; fi
  if [[ "$(printf '%s\n' "$ARGVTEXT" | grep -c '^workspace list$')" -eq 1 ]]; then
    pass; else fail "workspace list: expected one cached read, got ARGV=$ARGVTEXT"; fi

  # 2. A pane starts as its kind; the workspace row above already names the
  #    agent, and the first dispatch relabels the pane with the role.
  if printf '%s' "$ARGVTEXT" | grep -q "pane rename w3:p1 codex" \
     && printf '%s' "$ARGVTEXT" | grep -q "pane rename w4:p1 grok"; then
    pass; else fail "pane label: expected the kind alone, got ARGV=$ARGVTEXT"; fi

  # 5. The lead's own pane is a caller, not a worker (w1:p1 is excluded).
  if ! printf '%s' "$ARGVTEXT" | grep -q "pane rename w1:p1"; then
    pass; else fail "caller: the lead's pane must not be relabelled as a worker"; fi

  # 3. A workspace already carrying its name is left alone.
  printf '{"id":"cli:workspace:list","result":{"workspaces":[{"workspace_id":"w1","name":"lead"},{"workspace_id":"w3","name":"codex"},{"workspace_id":"w4","name":"grok"}]}}\n' > "$WS" \
    || die "could not rewrite the workspace list"
  ARGS=(lead)
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '
      .lead.state == "unchanged" and (.agents | map(.workspace) | unique == ["unchanged"])' >/dev/null 2>&1; then
    pass; else fail "idempotent: expected unchanged, got RC=$RC OUT=$OUT"; fi
  if ! printf '%s' "$ARGVTEXT" | grep -q "workspace rename"; then
    pass; else fail "idempotent: nothing may be sent for a name already in place"; fi

  # 4. An explicit pair wins over the roster's workspace id.
  ARGS=(lead codex=w9)
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$ARGVTEXT" | grep -q "workspace rename w9 codex"; then
    pass; else fail "override: expected w9, got ARGV=$ARGVTEXT"; fi

  # 6. A label is cosmetic: a failure is reported, and the rest still run.
  printf '{"id":"cli:workspace:list","result":{"workspaces":[{"workspace_id":"w1","name":""},{"workspace_id":"w3","name":""},{"workspace_id":"w4","name":""}]}}\n' > "$WS" \
    || die "could not rewrite the workspace list"
  ARGS=(lead)
  run FAKE_RENAME_ERR="workspace rename w3"
  if [[ $RC -eq 3 ]] && printf '%s' "$OUT" | jq -e '
      (.agents[] | select(.name == "codex") | .workspace) == "failed"
      and ((.agents[] | select(.name == "grok") | .workspace) == "renamed")' >/dev/null 2>&1; then
    pass; else fail "rename failure: expected exit 3 naming it, got RC=$RC OUT=$OUT"; fi

  # 7. No roster, no labels.
  ARGS=(lead)
  run FAKE_LIST_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]]; then
    pass; else fail "roster failure: expected exit 2, got RC=$RC OUT=$OUT"; fi

  # A failed workspace inventory is a tool failure, never an empty inventory.
  ARGS=(lead)
  run FAKE_WS_ERR=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && [[ "$ERRTEXT" == *"workspace list"* ]]; then
    pass; else fail "workspace-list failure: expected exit 2 naming the command, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  ARGS=(lead)
  run FAKE_WS_BAD=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && [[ "$ERRTEXT" == *"workspace list payload"* ]]; then
    pass; else fail "malformed workspace list: expected exit 2 naming the payload, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi
  ARGS=(lead)
  run FAKE_WS_BAD_MEMBER=1
  if [[ $RC -eq 2 && -z "$OUT" ]] && [[ "$ERRTEXT" == *"workspace list payload"* ]]; then
    pass; else fail "malformed workspace member: expected exit 2 naming the payload, got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 8. Usage and environment.
  OUT="$(env HERDR_ENV=1 HERDR_WORKSPACE_ID=w1 HERDR_BIN="$FAKE" bash "$SCRIPT" 2>"$TMP/e8")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e8"; then
    pass; else fail "usage: expected exit 1, got RC=$RC"; fi
  OUT="$(env -u HERDR_ENV HERDR_BIN="$FAKE" bash "$SCRIPT" lead 2>"$TMP/e8b")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "Herdr" "$TMP/e8b"; then
    pass; else fail "outside Herdr: expected exit 1, got RC=$RC"; fi
  # `env` stops option parsing at the first assignment, so -u comes first.
  OUT="$(env -u HERDR_WORKSPACE_ID HERDR_ENV=1 HERDR_BIN="$FAKE" bash "$SCRIPT" lead 2>"$TMP/e8c")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "HERDR_WORKSPACE_ID" "$TMP/e8c"; then
    pass; else fail "no workspace id: expected exit 1, got RC=$RC"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
