#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/teamlead.sh.
#
# The launcher is copied into a temp skill dir per case, so the assertions are
# about the launcher's own behavior (interpreter probe, package probe, argument
# and PYTHONPATH forwarding) and never about the packaged module's logic. A fake
# interpreter records what it was handed (rules/testing-standards.md — fixtures
# built in setup, no network, no real Python module import).
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Forwarding      -> `-m teamlead` plus every argument, verbatim.
#   2. PYTHONPATH      -> the skill dir is prefixed, a prior value preserved.
#   3. Missing package -> exit 1 naming the reinstall command.
#   4. Missing python  -> exit 1 naming the interpreter.
#   5. Old Python      -> exit 1 naming the minimum version.
#   6. Broken Python   -> probe failure is distinct from an old version.
#   7. Exit passthrough-> the module's own status reaches the caller.
#   8. Legacy grammar -> Python 2.7/3.5 is diagnosed as unsupported.
#   9. Unreadable     -> malformed probe output names the repair.
#
# Run: bash skills/herdr-teamlead/tests/test_teamlead_launcher.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake interpreter: records argv and PYTHONPATH, exits with $FAKE_PY_RC.
mk_fake_python() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake interpreter at $1"
#!/usr/bin/env bash
set -uo pipefail
if [[ "${1:-}" == "-c" ]]; then
  # Parse the actual probe with Python's pre-f-string grammar. This catches
  # a modern-only probe before returning the legacy interpreter's version.
  if [[ "${FAKE_PY_LEGACY_GRAMMAR:-0}" == 1 ]]; then
    if ! python3 -c 'import ast, sys; ast.parse(sys.argv[1], feature_version=(3, 5))' "$2"; then
      exit 1
    fi
  fi
  if [[ "${FAKE_PY_VERSION_RC:-0}" -ne 0 ]]; then
    printf 'interpreter startup failed\n' >&2
    exit "$FAKE_PY_VERSION_RC"
  fi
  printf '%s\n' "${FAKE_PY_VERSION:-3.11}"
  exit 0
fi
printf 'ARGV: %s\n' "$*"
printf 'PYTHONPATH: %s\n' "${PYTHONPATH:-}"
exit "${FAKE_PY_RC:-0}"
FAKE
  chmod +x "$1" || die "could not chmod the fake interpreter at $1"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/teamlead.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "teamlead.sh not found/readable at $SCRIPT"

  TMP="$(mktemp -d -t teamlead-launcher-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  local skill="$TMP/skill"
  mkdir -p "$skill/teamlead" || die "could not create $skill/teamlead"
  cp "$SCRIPT" "$skill/teamlead.sh" || die "could not copy the launcher into $skill"
  local fakepy="$TMP/fakepy"
  mk_fake_python "$fakepy"

  FAIL=0; PASS=0

  # 1. Arguments reach the module unchanged, behind `-m teamlead`.
  local out rc
  out="$(env PY_BIN="$fakepy" bash "$skill/teamlead.sh" plan --roles developer,tester,reviewer 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -q -- "ARGV: -m teamlead plan --roles developer,tester,reviewer"; then
    pass; else fail "forwarding: expected the full argv behind -m teamlead, got rc=$rc out=$out"; fi

  # 2. The skill dir is prefixed onto PYTHONPATH, an existing value preserved.
  out="$(env PY_BIN="$fakepy" PYTHONPATH="/existing/path" bash "$skill/teamlead.sh" state 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -q "PYTHONPATH: ${skill}:/existing/path"; then
    pass; else fail "PYTHONPATH: expected the skill dir prefixed, got rc=$rc out=$out"; fi

  # 3. No packaged module -> refuse with the reinstall command.
  local bare="$TMP/bare"
  mkdir -p "$bare" || die "could not create $bare"
  cp "$SCRIPT" "$bare/teamlead.sh" || die "could not copy the launcher into $bare"
  out="$(env PY_BIN="$fakepy" bash "$bare/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "tessl install"; then
    pass; else fail "missing package: expected exit 1 naming the reinstall, got rc=$rc out=$out"; fi

  # 4. No interpreter -> refuse, naming what to install.
  out="$(env PY_BIN="$TMP/no-such-python" bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "no-such-python"; then
    pass; else fail "missing interpreter: expected exit 1 naming it, got rc=$rc out=$out"; fi

  # 5. An installed interpreter below the minimum version is refused early.
  out="$(env PY_BIN="$fakepy" FAKE_PY_VERSION=3.10 bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "Python 3.11"; then
    pass; else fail "old interpreter: expected exit 1 naming Python 3.11, got rc=$rc out=$out"; fi

  # A broken interpreter is not misreported as an old one.
  out="$(env PY_BIN="$fakepy" FAKE_PY_VERSION_RC=7 bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "failed while checking" \
     && printf '%s' "$out" | grep -q "exit 7"; then
    pass; else fail "broken interpreter: expected a distinct probe failure, got rc=$rc out=$out"; fi

  # 7. The module's own exit status is the launcher's exit status.
  out="$(env PY_BIN="$fakepy" FAKE_PY_RC=7 bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 7 ]]; then
    pass; else fail "exit passthrough: expected 7, got rc=$rc out=$out"; fi

  # 8. A legacy interpreter must reach the unsupported-version branch,
  # not fail to parse the probe. Both plausible legacy version values use
  # Python 3.5's pre-f-string grammar; no claim of a full Python 2 emulator.
  local legacy_version
  for legacy_version in 2.7 3.5; do
    out="$(env PY_BIN="$fakepy" FAKE_PY_VERSION="$legacy_version" FAKE_PY_LEGACY_GRAMMAR=1 \
      bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
    if [[ $rc -eq 1 && "$out" == *"older than Python 3.11"* ]]; then
      pass
    else
      fail "legacy $legacy_version: expected unsupported-version diagnosis, got rc=$rc out=$out"
    fi
  done

  # 9. Malformed output is an unreadable probe, not an unsupported version.
  out="$(env PY_BIN="$fakepy" FAKE_PY_VERSION=invalid bash "$skill/teamlead.sh" measure 2>&1)"; rc=$?
  if [[ $rc -eq 1 && "$out" == *"returned an unreadable version"* && "$out" == *"point PY_BIN"* ]]; then
    pass
  else
    fail "unreadable version: expected a repair instruction, got rc=$rc out=$out"
  fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
