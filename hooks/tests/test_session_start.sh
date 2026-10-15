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

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# mk_case <name>: a fresh fixture hooks dir holding a copy of the aggregator.
mk_case() {
  CASE="$TMP/$1/hooks"
  mkdir -p "$CASE" || die "mk_case $1: mkdir failed"
  cp "$SCRIPT" "$CASE/session-start.sh" || die "mk_case $1: copy failed"
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
  child env.sh 'jq -n --arg c "herdr=${HERDR_ENV-unset} mode=${SESSION_START_MODE-unset}" '"'"'{additionalContext: $c}'"'"
  run SESSION_START_HOOKS="env.sh" HERDR_ENV=1
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext == "herdr=1 mode=native"' >/dev/null 2>&1; then
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
  child env.sh 'jq -n --arg c "mode=${SESSION_START_MODE-unset}" '"'"'{additionalContext: $c}'"'"
  run SESSION_START_HOOKS="env.sh" TESSL_AGENT=cursor
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext == "mode=portable" and (has("hookSpecificOutput") | not)' >/dev/null 2>&1; then
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
  ctx_child one.sh "1"
  ctx_child two.sh "2"
  printf 'two.sh\n\none.sh\n' > "$CASE/session-start-hooks.txt" || die "c8 list write failed"
  run
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext == "2\n\n1"' >/dev/null 2>&1; then
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
  run SESSION_START_HOOKS="mark.sh" PATH="$bin"
  if [[ $RC -eq 0 && -e "$TMP/c9-ran" ]] && printf '%s' "$OUT" | jq -e '.hookSpecificOutput.additionalContext | test("jq is not installed")' >/dev/null 2>&1; then
    pass; else fail "no jq (native): expected the child to run and a jq status, got RC=$RC OUT=$OUT"; fi
  run SESSION_START_HOOKS="mark.sh" PATH="$bin" TESSL_AGENT=cursor
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | test("jq is not installed")' >/dev/null 2>&1; then
    pass; else fail "no jq (portable): expected a consensus jq status, got RC=$RC OUT=$OUT"; fi

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
