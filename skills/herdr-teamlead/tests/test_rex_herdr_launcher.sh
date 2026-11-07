#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/rex-herdr.sh.
#
# The launcher is copied into a temp skill dir per run, and a fake `uv`
# records what it was handed, so the assertions cover the launcher alone:
# uv probe, module probe, argument and PYTHONPATH forwarding, exit
# passthrough. The adapter's own behavior is tests/test_rex_herdr.py.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Forwarding      -> `uv run` with the 3.11+ constraint and no project or
#                         download, then `-m teamlead.rex_herdr` and every
#                         argument, verbatim.
#   2. PYTHONPATH      -> the skill dir is prefixed, a prior value preserved.
#   3. Exit passthrough-> the module's usage-error 2 reaches the caller.
#   4. Missing module  -> exit 1 naming the reinstall command.
#   5. Missing uv      -> exit 1 naming the uv install page.
#   6. Setup envelope  -> launcher failures are Herdr's stderr JSON, code
#                         setup_error, even for a path holding a double quote.
#
# Run: bash skills/herdr-teamlead/tests/test_rex_herdr_launcher.sh
set -uo pipefail

SUMMARY_TOKEN="${TEST_SUMMARY_TOKEN:-}"
unset TEST_SUMMARY_TOKEN

die() { echo "fatal: $*" >&2; exit 2; }

cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

# A fake uv: records argv and PYTHONPATH and exits with $FAKE_UV_RC.
mk_fake_uv() { # <path>
  cat > "$1" <<'FAKE' || die "could not write the fake uv at $1"
#!/usr/bin/env bash
set -uo pipefail
printf 'ARGV: %s\n' "$*"
printf 'PYTHONPATH: %s\n' "${PYTHONPATH:-}"
exit "${FAKE_UV_RC:-0}"
FAKE
  chmod +x "$1" || die "could not chmod the fake uv at $1"
}

main() {
  local script
  script="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/rex-herdr.sh"
  [[ -f "$script" && -r "$script" ]] || die "rex-herdr.sh not found/readable at $script"

  TMP="$(mktemp -d -t rex-herdr-launcher-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT

  local skill="$TMP/skill" fakeuv="$TMP/fakeuv"
  mkdir -p "$skill/teamlead" || die "could not create $skill/teamlead"
  cp "$script" "$skill/rex-herdr.sh" || die "could not copy the launcher into $skill"
  : > "$skill/teamlead/rex_herdr.py" || die "could not create the module stub"
  mk_fake_uv "$fakeuv"

  FAIL=0; PASS=0
  local out rc

  # 1. Arguments reach the module unchanged, behind the pinned `uv run` form.
  out="$(env UV_BIN="$fakeuv" bash "$skill/rex-herdr.sh" agent prompt claude "two words" --wait 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -qx -- "ARGV: run --no-project --no-python-downloads --python >=3.11 python -m teamlead.rex_herdr agent prompt claude two words --wait"; then
    pass; else fail "forwarding: expected uv run … -m teamlead.rex_herdr plus the argv, got rc=$rc out=$out"; fi

  # 2. The skill dir is prefixed onto PYTHONPATH, an existing value preserved.
  out="$(env UV_BIN="$fakeuv" PYTHONPATH="/existing/path" bash "$skill/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]] && printf '%s' "$out" | grep -qx -- "PYTHONPATH: ${skill}:/existing/path"; then
    pass; else fail "PYTHONPATH: expected ${skill}:/existing/path, got rc=$rc out=$out"; fi

  # 3. The module's own exit status reaches the caller.
  out="$(env UV_BIN="$fakeuv" FAKE_UV_RC=2 bash "$skill/rex-herdr.sh" bogus 2>&1)"; rc=$?
  if [[ $rc -eq 2 ]]; then pass; else fail "passthrough: expected exit 2, got rc=$rc out=$out"; fi

  # 4. A missing module names the reinstall command and never execs.
  local bare="$TMP/bare"
  mkdir -p "$bare/teamlead" || die "could not create $bare/teamlead"
  cp "$script" "$bare/rex-herdr.sh" || die "could not copy the launcher into $bare"
  out="$(env UV_BIN="$fakeuv" bash "$bare/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "tessl install gamussa/coding-policy" && ! printf '%s' "$out" | grep -q "ARGV:"; then
    pass; else fail "missing module: expected exit 1 with the reinstall hint, got rc=$rc out=$out"; fi

  # 5. A missing uv is named with its install page and nothing runs.
  out="$(env UV_BIN="$TMP/no-such-uv" bash "$skill/rex-herdr.sh" agent list 2>&1)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | grep -q "no-such-uv' not found" && printf '%s' "$out" | grep -q "docs.astral.sh/uv"; then
    pass; else fail "missing uv: expected exit 1 naming uv and its install page, got rc=$rc out=$out"; fi

  # 6. A launcher failure is one valid JSON envelope, quotes escaped.
  local quoted="$TMP/sk\"ill"
  mkdir -p "$quoted/teamlead" || die "could not create $quoted/teamlead"
  cp "$script" "$quoted/rex-herdr.sh" || die "could not copy the launcher into $quoted"
  out="$(env UV_BIN="$fakeuv" bash "$quoted/rex-herdr.sh" agent read claude 2>&1 >/dev/null)"; rc=$?
  if [[ $rc -eq 1 ]] && printf '%s' "$out" | jq -e '.error.code == "setup_error" and (.error.message | contains("sk\"ill"))' >/dev/null; then
    pass; else fail "setup envelope: expected one setup_error JSON object, got rc=$rc out=$out"; fi

  echo "test-summary: passed=${PASS} failed=${FAIL} token=${SUMMARY_TOKEN}"
  (( FAIL == 0 ))
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
