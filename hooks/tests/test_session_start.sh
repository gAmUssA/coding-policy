#!/usr/bin/env bash
# Outcome-based tests for hooks/session-start.sh.
#
# Each case copies the aggregator into its OWN fixture hooks directory beside
# fake child hooks (mk_case), so cases share no state and run in any order
# (rules/testing-standards.md Fixtures and Independence). The harness drops
# `set -e` to aggregate results, so every fixture command is checked explicitly
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Native, last child silent -> every child's status arrives, joined in list
#                                   order, as hookSpecificOutput (the shape
#                                   `tessl hook run` reduced to nothing).
#   2. Native environment        -> a child sees HERDR_ENV and SESSION_START_MODE=native.
#   3. TESSL_AGENT=claude-code   -> silent, and no child runs.
#   4. TESSL_AGENT=cursor        -> consensus {"additionalContext"}; children see
#                                   SESSION_START_MODE=portable.
#   5. Child exits non-zero      -> its own status line naming the rerun command.
#   6. Child prints non-JSON     -> its own status line; other statuses kept.
#   7. Nothing to report         -> empty stdout, exit 0.
#   8. Hook list file            -> read in order; a missing list is reported.
#   9. No jq on PATH             -> one fixed status naming the jq install step,
#                                   in both native and portable shape.
#  11. Stalled child           -> reporting/stalled/reporting: the stalled child
#                                   is stopped at its deadline (its descendants
#                                   too), reported, and both other statuses arrive.
#  12. Total budget spent       -> a later child is reported as not run.
#  13. Real check-tessl-latest -> through the aggregator with a consumer
#                                   tessl.json and a recording fake `tessl`:
#                                   portable mode never runs `tessl update` and
#                                   never claims "latest"; native mode updates.
#  14. Child deadline env      -> each child sees its own deadline as
#                                   SESSION_START_CHILD_DEADLINE_SEC.
#  15. Status vs guide          -> a non-status context (the beans guide) listed
#                                   before a status never enters the status
#                                   block: statuses come first, the guide
#                                   follows under its own not-a-status header,
#                                   in both native and portable shape.
#  16. Header contract          -> the agent-context header the aggregator emits
#                                   appears verbatim, in backticks, in
#                                   rules/hook-action-reporting.md and ADR 0006.
#  10. Shipped wiring            -> the manifest declares only session-start.sh
#                                   (portable + native claude-code), and every
#                                   listed hook exists.
#
# Run: bash hooks/tests/test_session_start.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

#: The aggregator's header line opening each non-status context.
GUIDE_HEADER_PREFIX="Agent context, not a session-start status — do not relay it to the user"

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# mk_case <name>: a fresh fixture hooks dir holding a copy of the aggregator.
mk_case() {
  CASE="$TMP/$1/hooks"
  mkdir -p "$CASE" || die "mk_case $1: mkdir failed"
  cp "$SCRIPT" "$CASE/session-start.sh" || die "mk_case $1: copy failed"
  cp "$RUNNER" "$CASE/bounded-run.sh" || die "mk_case $1: runner copy failed"
}

# child <name> <body>: a fake child hook in the current case.
child() {
  printf '#!/usr/bin/env bash\n%s\n' "$2" > "$CASE/$1" || die "child $1: write failed"
}

# ctx_child <name> <text>: a child that emits one additionalContext object.
ctx_child() {
  child "$1" "jq -n --arg c '$2' '{additionalContext: \$c}'"
}

# run [env...] -> OUT, RC
run() {
  OUT="$(env "$@" bash "$CASE/session-start.sh" </dev/null 2>/dev/null)"
  RC=$?
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/session-start.sh"
  ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  [[ -r "$SCRIPT" ]] || die "aggregator not found at $SCRIPT"
  RUNNER="$(dirname "$SCRIPT")/bounded-run.sh"
  [[ -r "$RUNNER" ]] || die "deadline runner not found at $RUNNER"
  command -v jq >/dev/null 2>&1 || die "jq required for these tests"
  TMP="$(mktemp -d -t session-start-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  unset TESSL_AGENT HERDR_ENV SESSION_START_MODE SESSION_START_HOOKS

  # 1. Native, the last child silent: both statuses arrive, in order.
  mk_case c1
  ctx_child a.sh "Session-start status — A"
  child b.sh "exit 0"
  ctx_child c.sh "Session-start status — C"
  child d.sh "exit 0"
  run SESSION_START_HOOKS="a.sh b.sh c.sh d.sh"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.hookEventName == "SessionStart" and .hookSpecificOutput.additionalContext == "Session-start status — A\n\nSession-start status — C"' >/dev/null 2>&1; then
    pass; else fail "native merge: expected A and C joined, got RC=$RC OUT=$OUT"; fi

  # 2. The native entry keeps the session's environment.
  mk_case c2
  # shellcheck disable=SC2016 # expands inside the generated child, not here
  child env.sh 'jq -n --arg c "Session-start status — herdr=${HERDR_ENV-unset} mode=${SESSION_START_MODE-unset}" '"'"'{additionalContext: $c}'"'"
  run SESSION_START_HOOKS="env.sh" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext == "Session-start status — herdr=1 mode=native"' >/dev/null 2>&1; then
    pass; else fail "native env: expected HERDR_ENV and native mode, got RC=$RC OUT=$OUT"; fi

  # 3. Under tessl for claude-code the native entry already ran: silent, no child.
  mk_case c3
  child mark.sh "touch '$TMP/c3-ran'"
  run SESSION_START_HOOKS="mark.sh" TESSL_AGENT=claude-code
  if [[ $RC -eq 0 && -z "$OUT" && ! -e "$TMP/c3-ran" ]]; then
    pass; else fail "tessl claude-code: expected silence and no child run, got RC=$RC OUT=$OUT"; fi

  # 4. Under tessl for another agent: consensus payload, portable mode.
  mk_case c4
  # shellcheck disable=SC2016 # expands inside the generated child, not here
  child env.sh 'jq -n --arg c "Session-start status — mode=${SESSION_START_MODE-unset}" '"'"'{additionalContext: $c}'"'"
  run SESSION_START_HOOKS="env.sh" TESSL_AGENT=cursor
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext == "Session-start status — mode=portable" and (has("hookSpecificOutput") | not)' >/dev/null 2>&1; then
    pass; else fail "portable: expected consensus payload in portable mode, got RC=$RC OUT=$OUT"; fi

  # 5. A failing child is reported, never dropped.
  mk_case c5
  child bad.sh "exit 3"
  ctx_child ok.sh "Session-start status — ok"
  run SESSION_START_HOOKS="bad.sh ok.sh"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("hook bad.sh exited 3") and test("bash .*/bad.sh") and test("Session-start status — ok")' >/dev/null 2>&1; then
    pass; else fail "failing child: expected its status line, got RC=$RC OUT=$OUT"; fi

  # 6. A child printing non-JSON is reported; the others are kept.
  mk_case c6
  child noise.sh "echo not json"
  ctx_child ok.sh "Session-start status — ok"
  run SESSION_START_HOOKS="noise.sh ok.sh"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("hook noise.sh printed something other") and test("Session-start status — ok")' >/dev/null 2>&1; then
    pass; else fail "non-JSON child: expected its status line, got RC=$RC OUT=$OUT"; fi

  # 7. Nothing to report: empty stdout.
  mk_case c7
  child quiet.sh "exit 0"
  run SESSION_START_HOOKS="quiet.sh"
  if [[ $RC -eq 0 && -z "$OUT" ]]; then pass; else fail "silent: expected no output, got RC=$RC OUT=$OUT"; fi

  # 8. The list file sets the order; a missing list is a status, not silence.
  mk_case c8
  ctx_child one.sh "Session-start status — 1"
  ctx_child two.sh "Session-start status — 2"
  printf 'two.sh\n\none.sh\n' > "$CASE/session-start-hooks.txt" || die "c8 list write failed"
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext == "Session-start status — 2\n\nSession-start status — 1"' >/dev/null 2>&1; then
    pass; else fail "list file: expected list order 2 then 1, got RC=$RC OUT=$OUT"; fi
  rm "$CASE/session-start-hooks.txt" || die "c8 list remove failed"
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("could not read its hook list")' >/dev/null 2>&1; then
    pass; else fail "missing list: expected a status line, got RC=$RC OUT=$OUT"; fi

  # 9. No jq on PATH: the children still run; one fixed status names jq.
  mk_case c9
  child mark.sh "touch '$TMP/c9-ran'"
  local bin="$TMP/c9-bin"
  mkdir -p "$bin" || die "c9 bin mkdir failed"
  ln -sf "$(command -v bash)" "$bin/bash" || die "c9 link bash failed"
  ln -sf "$(command -v touch)" "$bin/touch" || die "c9 link touch failed"
  ln -sf "$(command -v sleep)" "$bin/sleep" || die "c9 link sleep failed"
  ln -sf "$(command -v ps)" "$bin/ps" || die "c9 link ps failed"
  ln -sf "$(command -v awk)" "$bin/awk" || die "c9 link awk failed"
  run SESSION_START_HOOKS="mark.sh" PATH="$bin"
  if [[ $RC -eq 0 && -e "$TMP/c9-ran" ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("jq is not installed")' >/dev/null 2>&1; then
    pass; else fail "no jq (native): expected the child to run and a jq status, got RC=$RC OUT=$OUT"; fi
  run SESSION_START_HOOKS="mark.sh" PATH="$bin" TESSL_AGENT=cursor
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("jq is not installed")' >/dev/null 2>&1; then
    pass; else fail "no jq (portable): expected a consensus jq status, got RC=$RC OUT=$OUT"; fi

  # 11. reporting / stalled / reporting under a 1s child deadline.
  mk_case c11
  ctx_child a.sh "Session-start status — A"
  # shellcheck disable=SC2016 # expands inside the generated child, not here
  # The grandchild touches a marker only if nothing killed it: an outcome,
  # never elapsed time.
  child stall.sh '(sleep 8; touch "'"$TMP"'/c11.done") & echo $! > "'"$TMP"'/c11-gc.pid"; wait'
  ctx_child c.sh "Session-start status — C"
  local gc
  run SESSION_START_HOOKS="a.sh stall.sh c.sh" SESSION_START_CHILD_BUDGET_SEC=1
  gc="$(cat "$TMP/c11-gc.pid")" || die "c11 grandchild pid missing"
  if [[ $RC -eq 0 && ! -e "$TMP/c11.done" ]] && ! kill -0 "$gc" 2>/dev/null \
     && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("^Session-start status — A\n\n") and test("hook stall.sh ran past its deadline") and test("Session-start status — C$")' >/dev/null 2>&1; then
    pass; else fail "stalled child: expected A, a deadline line, C, and the grandchild killed, got RC=$RC OUT=$OUT"; fi
  if kill -0 "$gc" 2>/dev/null; then kill -KILL "$gc" || echo "warn: could not stop $gc" >&2; fi

  # 12. The total budget runs out before the last child starts.
  mk_case c12
  child slow.sh "sleep 2"
  ctx_child late.sh "Session-start status — late"
  run SESSION_START_HOOKS="slow.sh late.sh" SESSION_START_BUDGET_SEC=1
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("hook late.sh did not run") and (test("Session-start status — late") | not)' >/dev/null 2>&1; then
    pass; else fail "budget spent: expected late.sh reported as not run, got RC=$RC OUT=$OUT"; fi

  # 13. The real check-tessl-latest child, portable then native.
  local proj="$TMP/c13-proj" fakebin="$TMP/c13-bin" log="$TMP/c13-tessl.log" mode
  mk_case c13
  cp "$(dirname "$SCRIPT")/check-tessl-latest.sh" "$CASE/check-tessl-latest.sh" || die "c13 copy child failed"
  mkdir -p "$proj/.tessl/plugins/gamussa/coding-policy" "$fakebin" || die "c13 mkdir failed"
  printf '{"dependencies":{"gamussa/coding-policy":{"version":"latest"}}}\n' > "$proj/tessl.json" || die "c13 manifest failed"
  # shellcheck disable=SC2016 # expands inside the generated fake, not here
  printf '#!/usr/bin/env bash\nprintf "%%s\\n" "$*" >> "%s"\nif [[ "$1" == update ]]; then printf "{\\"version\\":\\"1.1.0\\"}\\n" > .tessl/plugins/gamussa/coding-policy/tessl-package.json; fi\n' "$log" > "$fakebin/tessl" \
    || die "c13 fake tessl failed"
  chmod +x "$fakebin/tessl" || die "c13 chmod failed"
  for mode in portable native; do
    printf '{"version":"1.0.0"}\n' > "$proj/.tessl/plugins/gamussa/coding-policy/tessl-package.json" || die "c13 state reset failed"
    rm -f "$log" || die "c13 log reset failed"
    if [[ "$mode" == portable ]]; then
      OUT="$(cd "$proj" && env SESSION_START_HOOKS=check-tessl-latest.sh TESSL_AGENT=cursor PATH="$fakebin:$PATH" bash "$CASE/session-start.sh" </dev/null 2>/dev/null)"; RC=$?
      if [[ $RC -eq 0 && ! -e "$log" ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("gamussa/coding-policy 1.0.0") and test("not updated") and (test("latest\\)") | not)' >/dev/null 2>&1; then
        pass; else fail "portable check-tessl-latest: expected no tessl call and an installed-only status, got RC=$RC log=$(cat "$log" 2>/dev/null) OUT=$OUT"; fi
    else
      OUT="$(cd "$proj" && env SESSION_START_HOOKS=check-tessl-latest.sh PATH="$fakebin:$PATH" bash "$CASE/session-start.sh" </dev/null 2>/dev/null)"; RC=$?
      if [[ $RC -eq 0 ]] && grep -qx 'update --yes' "$log" && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("1.0.0 → 1.1.0")' >/dev/null 2>&1; then
        pass; else fail "native check-tessl-latest: expected tessl update and a transition, got RC=$RC log=$(cat "$log" 2>/dev/null) OUT=$OUT"; fi
    fi
  done

  # 14. The child's deadline is exported to it.
  mk_case c14
  # shellcheck disable=SC2016 # expands inside the generated child, not here
  child dl.sh 'jq -n --arg c "Session-start status — deadline=${SESSION_START_CHILD_DEADLINE_SEC-unset}" '"'"'{additionalContext: $c}'"'"
  run SESSION_START_HOOKS="dl.sh" SESSION_START_CHILD_BUDGET_SEC=7
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext == "Session-start status — deadline=7"' >/dev/null 2>&1; then
    pass; else fail "child deadline env: expected deadline=7, got RC=$RC OUT=$OUT"; fi

  # 15. The beans guide is agent context, not a status: it must sit after
  # every status, under a header that ends the relayable block.
  mk_case c15
  ctx_child guide.sh "BEANS-GUIDE line one
BEANS-GUIDE line two"
  ctx_child st.sh "Session-start status — git: in sync"
  local mode15 path15
  for mode15 in native portable; do
    if [[ "$mode15" == native ]]; then
      run SESSION_START_HOOKS="guide.sh st.sh"; path15='.hookSpecificOutput.additionalContext'
    else
      run SESSION_START_HOOKS="guide.sh st.sh" TESSL_AGENT=cursor; path15='.additionalContext'
    fi
    if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e --arg h "$GUIDE_HEADER_PREFIX" "$path15"' as $c
        | ($c | index($h)) as $at
        | $at != null
        and ($c[:$at] | test("^Session-start status — git: in sync")) and ($c[:$at] | test("BEANS") | not)
        and ($c[$at:] | test("guide.sh") and test("BEANS-GUIDE line one\nBEANS-GUIDE line two"))' >/dev/null 2>&1; then
      pass; else fail "status vs guide ($mode15): expected statuses first and the guide under its own header, got RC=$RC OUT=$OUT"; fi
  done

  # 16. The header the aggregator emits is the literal the rule and the ADR
  # name, so an agent following the rule stops relaying exactly there.
  mk_case c16
  ctx_child guide.sh "guide text"
  run SESSION_START_HOOKS="guide.sh"
  local header16
  header16="$(printf '%s' "$OUT" | jq -r '.hookSpecificOutput.additionalContext' | head -n 1)" || die "c16 could not read the emitted header"
  header16="${header16% (from guide.sh):}"
  if [[ -n "$header16" && "$header16" != *"(from"* ]] \
     && grep -qF -- "\`${header16}\`" "$ROOT/rules/hook-action-reporting.md" \
     && grep -qF -- "\`${header16} (from <hook>):\`" "$ROOT/docs/adr/0006-native-aggregating-session-start.md"; then
    pass; else fail "header contract: emitted header '${header16}' is not the literal named in rules/hook-action-reporting.md and ADR 0006"; fi

  # 10. Shipped wiring: one SessionStart entry per tier, and the list resolves.
  local manifest="$ROOT/.tessl-plugin/plugin.json"
  if jq -e '
      ([.hooks.SessionStart[].hooks[]] | length == 1 and .[0].args == ["${TESSL_PLUGIN_DIR}/hooks/session-start.sh"])
      and ([.nativeHooks["claude-code"].SessionStart[].hooks[]] | length == 1 and .[0].command == "bash \"${TESSL_PLUGIN_DIR}/hooks/session-start.sh\"")
    ' "$manifest" >/dev/null 2>&1; then
    pass; else fail "manifest: expected session-start.sh as the only portable and native claude-code SessionStart hook"; fi
  local missing="" name count=0
  while IFS= read -r name || [[ -n "$name" ]]; do
    [[ -n "$name" ]] || continue
    count=$((count+1))
    [[ -f "$ROOT/hooks/$name" ]] || missing+=" $name"
  done < "$ROOT/hooks/session-start-hooks.txt"
  if [[ -z "$missing" && $count -gt 0 ]]; then
    pass; else fail "hook list: missing files:${missing} (count=$count)"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
