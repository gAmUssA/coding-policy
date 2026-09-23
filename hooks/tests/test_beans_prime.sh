#!/usr/bin/env bash
# Outcome-based tests for hooks/beans-prime.sh.
#
# A fake `beans` on PATH stands in for the CLI, so the suite runs offline and
# without beans installed. Every case builds its OWN repo, fake CLI, and output
# files under a fresh directory (mk_case), so cases share no state and run in
# any order (rules/testing-standards.md Fixtures and Independence). The harness
# drops `set -e` to aggregate results, so every fixture command is checked
# explicitly (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. No .beans.yml         -> silent, exit 0.
#   2. Beans repo, CLI works -> additionalContext carries `beans prime` stdout verbatim.
#   3. Nested directory      -> .beans.yml found in an ancestor; CLI runs from that root.
#   4. CLI missing           -> stderr names the install step, stdout empty, exit 0.
#   5. CLI fails             -> stderr names the failure, stdout empty, exit 0.
#   6. CLI prints nothing    -> stderr warns, stdout empty, exit 0.
#
# Run: bash hooks/tests/test_beans_prime.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# mk_case <name>: a fresh, independent fixture root. Sets CASE (its dir), TOOLS (a PATH dir holding
# only a link to jq, so no real `beans` can be found), and REPO (a beans repo) — the repo exists in
# every case; a case that wants a plain directory uses $CASE/plain.
mk_case() {
  CASE="$TMP/$1"
  mkdir -p "$CASE/plain" "$CASE/tools" "$CASE/repo/src/deep" || die "mk_case $1: mkdir failed"
  ln -s "$(command -v jq)" "$CASE/tools/jq" || die "mk_case $1: jq link failed"
  printf 'beans:\n  path: .beans\n' > "$CASE/repo/.beans.yml" || die "mk_case $1: .beans.yml failed"
  TOOLS="$CASE/tools"; REPO="$CASE/repo"
}

# mk_fake <case-dir> <mode>: a `beans` under <case-dir>/bin whose `prime` behaves per <mode>
# (ok | fail | empty) and records its working directory in <case-dir>/fake-cwd. Echoes the bin dir.
mk_fake() {
  local bin="$1/bin"
  mkdir -p "$bin" || die "mk_fake: mkdir failed"
  cat > "$bin/beans" <<EOF || die "mk_fake: write failed"
#!/usr/bin/env bash
[[ "\${1:-}" == "prime" ]] || { echo "unexpected: \$*" >&2; exit 9; }
pwd -P > "$1/fake-cwd"
case "$2" in
  ok)    printf '# Beans Usage Guide\n\nUse beans create "Title" -t task.\n' ;;
  fail)  echo "no config found" >&2; exit 1 ;;
  empty) : ;;
esac
EOF
  chmod +x "$bin/beans" || die "mk_fake: chmod failed"
  printf '%s' "$bin"
}

# run_hook <start-dir> <PATH> -> OUT, ERR, RC
run_hook() {
  OUT="$(BEANS_PRIME_START="$1" PATH="$2" bash "$HOOK" </dev/null 2>"$CASE/err")"; RC=$?
  ERR="$(cat "$CASE/err")"
}

main() {
  HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/beans-prime.sh"
  [[ -r "$HOOK" ]] || die "hook not found at $HOOK"
  TMP="$(mktemp -d -t beans-prime-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  local bin

  # 1. Not a beans repo: silent.
  mk_case plain; bin="$(mk_fake "$CASE" ok)"
  run_hook "$CASE/plain" "$bin:$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 && -z "$OUT" && -z "$ERR" ]]; then pass; else fail "plain dir: expected silence, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 2. Beans repo with a working CLI: the guide is the payload, verbatim.
  mk_case guide; bin="$(mk_fake "$CASE" ok)"
  run_hook "$REPO" "$bin:$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 && -z "$ERR" ]] && [[ "$(printf '%s' "$OUT" | jq -r .additionalContext)" == $'# Beans Usage Guide\n\nUse beans create "Title" -t task.' ]]; then
    pass; else fail "beans repo: expected the guide as additionalContext, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 3. Nested directory: the ancestor's .beans.yml is found and the CLI runs from that root.
  mk_case nested; bin="$(mk_fake "$CASE" ok)"
  run_hook "$REPO/src/deep" "$bin:$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | contains("Beans Usage Guide")' >/dev/null \
     && [[ "$(cd "$REPO" && pwd -P)" == "$(cat "$CASE/fake-cwd")" ]]; then
    pass; else fail "nested dir: expected the guide from the repo root, got RC=$RC OUT=$OUT cwd=$(cat "$CASE/fake-cwd" 2>/dev/null)"; fi

  # 4. Beans repo, CLI missing: actionable stderr, no payload.
  mk_case missing
  run_hook "$REPO" "$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"beans CLI is not on PATH"* ]] && [[ "$ERR" == *"brew install beans"* ]]; then
    pass; else fail "missing CLI: expected install guidance, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 5. CLI fails: the failure and its diagnostic are named, no payload.
  mk_case failing; bin="$(mk_fake "$CASE" fail)"
  run_hook "$REPO" "$bin:$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"beans prime failed (exit 1)"* ]] && [[ "$ERR" == *"no config found"* ]]; then
    pass; else fail "failing CLI: expected a named failure, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  # 6. CLI prints nothing: warned, no payload.
  mk_case empty; bin="$(mk_fake "$CASE" empty)"
  run_hook "$REPO" "$bin:$TOOLS:/usr/bin:/bin"
  if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"printed nothing"* ]]; then
    pass; else fail "empty CLI: expected a warning, got RC=$RC OUT=$OUT ERR=$ERR"; fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; return 1; fi
  echo "PASSED: all ${PASS} checks" >&2
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
