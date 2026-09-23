#!/usr/bin/env bash
# Outcome-based tests for hooks/beans-prime.sh.
#
# A fake `beans` on PATH stands in for the CLI, so the suite runs offline and
# without beans installed. Each scenario builds its own directory tree; the
# harness drops `set -e` to aggregate results, so every fixture command is
# checked explicitly (rules/error-handling.md aggregate-reporting carve-out).
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

HOOK="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/beans-prime.sh"
[[ -r "$HOOK" ]] || die "hook not found at $HOOK"
TMP="$(mktemp -d -t beans-prime-test.XXXXXX)" || die "mktemp failed"
trap cleanup EXIT

FAIL=0; PASS=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# mk_fake <dir> <mode>: a `beans` whose `prime` behaves per <mode>: ok | fail | empty
mk_fake() {
  mkdir -p "$1" || die "mkdir $1 failed"
  cat > "$1/beans" <<EOF || die "fake write failed"
#!/usr/bin/env bash
[[ "\${1:-}" == "prime" ]] || { echo "unexpected: \$*" >&2; exit 9; }
pwd -P > "$TMP/fake-cwd"
case "$2" in
  ok)    printf '# Beans Usage Guide\n\nUse beans create "Title" -t task.\n' ;;
  fail)  echo "no config found" >&2; exit 1 ;;
  empty) : ;;
esac
EOF
  chmod +x "$1/beans" || die "chmod failed"
}

# run_hook <start-dir> <PATH> -> OUT, ERR, RC
run_hook() {
  OUT="$(BEANS_PRIME_START="$1" PATH="$2" bash "$HOOK" </dev/null 2>"$TMP/err")"; RC=$?
  ERR="$(cat "$TMP/err")"
}

# A PATH that can hold no real beans: the system dirs plus a link to jq alone.
mkdir -p "$TMP/tools" || die "mkdir tools failed"
ln -s "$(command -v jq)" "$TMP/tools/jq" || die "jq link failed"
BASE_PATH="$TMP/tools:/usr/bin:/bin"

# 1. Not a beans repo: silent.
mkdir -p "$TMP/plain" || die "mkdir failed"
mk_fake "$TMP/bin-ok" ok
run_hook "$TMP/plain" "$TMP/bin-ok:$BASE_PATH"
if [[ $RC -eq 0 && -z "$OUT" && -z "$ERR" ]]; then pass; else fail "plain dir: expected silence, got RC=$RC OUT=$OUT ERR=$ERR"; fi

# 2. Beans repo with a working CLI: the guide is the payload, verbatim.
mkdir -p "$TMP/repo" || die "mkdir failed"
printf 'beans:\n  path: .beans\n' > "$TMP/repo/.beans.yml" || die "fixture failed"
run_hook "$TMP/repo" "$TMP/bin-ok:$BASE_PATH"
if [[ $RC -eq 0 && -z "$ERR" ]] && [[ "$(printf '%s' "$OUT" | jq -r .additionalContext)" == $'# Beans Usage Guide\n\nUse beans create "Title" -t task.' ]]; then
  pass; else fail "beans repo: expected the guide as additionalContext, got RC=$RC OUT=$OUT ERR=$ERR"; fi

# 3. Nested directory: the ancestor's .beans.yml is found and the CLI runs there.
mkdir -p "$TMP/repo/src/deep" || die "mkdir failed"
run_hook "$TMP/repo/src/deep" "$TMP/bin-ok:$BASE_PATH"
if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.additionalContext | contains("Beans Usage Guide")' >/dev/null \
   && [[ "$(cd "$TMP/repo" && pwd -P)" == "$(cat "$TMP/fake-cwd")" ]]; then
  pass; else fail "nested dir: expected the guide from the repo root, got RC=$RC OUT=$OUT cwd=$(cat "$TMP/fake-cwd" 2>/dev/null)"; fi

# 4. Beans repo, CLI missing: actionable stderr, no payload.
run_hook "$TMP/repo" "$BASE_PATH"
if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"beans CLI is not on PATH"* ]] && [[ "$ERR" == *"brew install beans"* ]]; then
  pass; else fail "missing CLI: expected install guidance, got RC=$RC OUT=$OUT ERR=$ERR"; fi

# 5. CLI fails: the failure and its diagnostic are named, no payload.
mk_fake "$TMP/bin-fail" fail
run_hook "$TMP/repo" "$TMP/bin-fail:$BASE_PATH"
if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"beans prime failed (exit 1)"* ]] && [[ "$ERR" == *"no config found"* ]]; then
  pass; else fail "failing CLI: expected a named failure, got RC=$RC OUT=$OUT ERR=$ERR"; fi

# 6. CLI prints nothing: warned, no payload.
mk_fake "$TMP/bin-empty" empty
run_hook "$TMP/repo" "$TMP/bin-empty:$BASE_PATH"
if [[ $RC -eq 0 && -z "$OUT" ]] && [[ "$ERR" == *"printed nothing"* ]]; then
  pass; else fail "empty CLI: expected a warning, got RC=$RC OUT=$OUT ERR=$ERR"; fi

echo "─────────────────────────────────────────────" >&2
if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
echo "PASSED: all ${PASS} checks" >&2
