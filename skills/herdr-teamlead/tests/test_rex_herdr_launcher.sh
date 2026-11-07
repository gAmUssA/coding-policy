#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/rex-herdr.sh.
#
# The launcher is copied into a temp skill dir per run, and a fake interpreter
# records what it was handed, so the assertions cover the launcher alone:
# interpreter probe, module probe, argument and PYTHONPATH forwarding, exit
# passthrough. The adapter's own behavior is tests/test_rex_herdr.py.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Forwarding      -> `-m teamlead.rex_herdr` plus every argument, verbatim.
#   2. PYTHONPATH      -> the skill dir is prefixed, a prior value preserved.
#   3. Exit passthrough-> the module's usage-error 2 reaches the caller.
#   4. Missing module  -> exit 1 naming the reinstall command.
#   5. Missing python  -> exit 1 naming the interpreter.
#   6. Old Python      -> exit 1 naming the minimum version.
#
# Run: bash skills/herdr-teamlead/tests/test_rex_herdr_launcher.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake interpreter: answers the version probe from $FAKE_PY_OK, otherwise
# records argv and PYTHONPATH and exits with $FAKE_PY_RC.
mk_fake_python() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake interpreter at $1"
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-}" == "-c" ]]; then
  exit "${FAKE_PY_PROBE_RC:-0}"
fi
printf 'ARGV: %s\n' "$*"
printf 'PYTHONPATH: %s\n' "${PYTHONPATH:-}"
exit "${FAKE_PY_RC:-0}"
FAKE
  chmod +x "$1" || die "could not chmod the fake interpreter at $1"
}

main() {
  local script
  script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/rex-herdr.sh"
  [[ -f "$script" && -r "$script" ]] || die "rex-herdr.sh not found/readable at $script"

  TMP="$(mktemp -d -t rex-herdr-launcher-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  local skill="$TMP/skill" fakepy="$TMP/fakepy"
  mkdir -p "$skill/teamlead" || die "could not create $skill/teamlead"
  cp "$script" "$skill/rex-herdr.sh" || die "could not copy the launcher into $skill"
  : > "$skill/teamlead/rex_herdr.py" || die "could not create the module stub"
  mk_fake_python "$fakepy"

  FAIL=0; PASS=0
  local out rc

  # 1. Arguments reach the module unchanged, behind `-m teamlead.rex_herdr`.
  out="$(env PY_BIN="$fakepy" bash "$skill/rex-herdr.sh" agent prompt claude "two words" --wait 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -qx -- "ARGV: -m teamlead.rex_herdr agent prompt claude two words --wait"; then
    pass; else fail "forwarding: expected the argv behind -m teamlead.rex_herdr, got rc=$rc out=$out"; fi

  # 2. The skill dir is prefixed onto PYTHONPATH, an existing value preserved.
  out="$(env PY_BIN="$fakepy" PYTHONPATH="/existing/path" bash "$skill/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -qx -- "PYTHONPATH: ${skill}:/existing/path"; then
    pass; else fail "PYTHONPATH: expected ${skill}:/existing/path, got rc=$rc out=$out"; fi

  # 3. The module's own exit status reaches the caller.
  out="$(env PY_BIN="$fakepy" FAKE_PY_RC=2 bash "$skill/rex-herdr.sh" bogus 2>&1)"; rc=$?
  if [[ $rc -eq 2 ]]; then pass; else fail "passthrough: expected exit 2, got rc=$rc out=$out"; fi

  # 4. A missing module names the reinstall command and never execs.
  local bare="$TMP/bare"
  mkdir -p "$bare/teamlead" || die "could not create $bare/teamlead"
  cp "$script" "$bare/rex-herdr.sh" || die "could not copy the launcher into $bare"
  out="$(env PY_BIN="$fakepy" bash "$bare/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "tessl install gamussa/coding-policy" && ! printf '%s' "$out" | grep -q "ARGV:"; then
    pass; else fail "missing module: expected exit 1 with the reinstall hint, got rc=$rc out=$out"; fi

  # 5. A missing interpreter is named.
  out="$(env PY_BIN="$TMP/no-such-python" bash "$skill/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "no-such-python' not found"; then
    pass; else fail "missing python: expected exit 1 naming the interpreter, got rc=$rc out=$out"; fi

  # 6. An interpreter older than 3.11 is refused before the module runs.
  out="$(env PY_BIN="$fakepy" FAKE_PY_PROBE_RC=1 bash "$skill/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "is not Python 3.11+" && ! printf '%s' "$out" | grep -q "ARGV:"; then
    pass; else fail "old python: expected exit 1 naming 3.11, got rc=$rc out=$out"; fi

  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
