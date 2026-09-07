#!/usr/bin/env bash
# Outcome-based tests for run-diagnostics.sh — the language-diagnostics
# gate. The two engines (shellcheck, pyright) are stubbed on PATH so the
# test asserts the runner's own behavior — exit-code aggregation and that
# both engines run even when the first fails — without invoking the real
# tools. Each case runs the runner as a subprocess against a throwaway
# fixture tree; no shared mutable state, order-independent.
#
# Run: bash scripts/tests/test_run_diagnostics.sh
# Exit 0 on all-pass; non-zero with a per-test diagnostic on failure.

# shellcheck disable=SC2329  # test cases run indirectly via run() ("$@" dispatch); shellcheck cannot trace dynamic invocation
set -uo pipefail

RUNNER="$(cd "$(dirname "$0")/.." && pwd)/run-diagnostics.sh"
[[ -x "$RUNNER" ]] || { echo "fatal: run-diagnostics.sh not executable at $RUNNER" >&2; exit 2; }

FAIL_COUNT=0
PASS_COUNT=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    return 0
  fi
  echo "    FAIL: ${label}: expected '${expected}', got '${actual}'" >&2
  return 1
}

run() {
  local name="$1"; shift
  if "$@"; then
    PASS_COUNT=$((PASS_COUNT + 1)); echo "  pass: $name"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1)); echo "  FAIL: $name" >&2
  fi
}

# Build a fixture: a base tree with a scripts/ dir holding one shell
# script, plus a PATH dir with stub shellcheck/pyright executables whose
# exit codes are read from env vars and whose invocations are logged.
make_fixture() {
  local d; d=$(mktemp -d)
  mkdir -p "$d/base/scripts"
  printf '#!/usr/bin/env bash\necho hi\n' > "$d/base/scripts/dummy.sh"
  mkdir -p "$d/bin"
  cat > "$d/bin/shellcheck" <<'STUB'
#!/usr/bin/env bash
echo "shellcheck $*" >> "$STUB_LOG"
exit "${STUB_SHELLCHECK_RC:-0}"
STUB
  cat > "$d/bin/pyright" <<'STUB'
#!/usr/bin/env bash
echo "pyright $*" >> "$STUB_LOG"
exit "${STUB_PYRIGHT_RC:-0}"
STUB
  chmod +x "$d/bin/shellcheck" "$d/bin/pyright"
  echo "$d"
}

# Invoke the runner against a fixture with stubbed engines. Args:
# $1=fixture-dir $2=shellcheck-rc $3=pyright-rc. Sets globals RC and LOG.
invoke() {
  local d="$1" sc_rc="$2" py_rc="$3"
  LOG="$d/stub.log"; : > "$LOG"
  RC=0
  PATH="$d/bin:$PATH" STUB_LOG="$LOG" STUB_SHELLCHECK_RC="$sc_rc" STUB_PYRIGHT_RC="$py_rc" \
    bash "$RUNNER" "$d/base" >/dev/null 2>&1 || RC=$?
}

t_both_clean_exits_zero() {
  local d; d=$(make_fixture)
  invoke "$d" 0 0
  rm -rf "$d"
  assert_eq "exit" "0" "$RC"
}

t_shellcheck_finding_exits_one() {
  local d; d=$(make_fixture)
  invoke "$d" 1 0
  rm -rf "$d"
  assert_eq "exit" "1" "$RC"
}

t_pyright_finding_exits_one() {
  local d; d=$(make_fixture)
  invoke "$d" 0 1
  rm -rf "$d"
  assert_eq "exit" "1" "$RC"
}

t_both_findings_exit_one() {
  local d; d=$(make_fixture)
  invoke "$d" 1 1
  rm -rf "$d"
  assert_eq "exit" "1" "$RC"
}

# Regression guard: a shellcheck finding must NOT short-circuit pyright —
# both engines run so one invocation surfaces every finding.
t_pyright_runs_even_when_shellcheck_fails() {
  local d; d=$(make_fixture)
  invoke "$d" 1 0
  local py_calls; py_calls=$(grep -c '^pyright ' "$d/stub.log")
  rm -rf "$d"
  assert_eq "pyright invoked despite shellcheck failure" "1" "$py_calls"
}

# Regression guard for #199: the `.github/codex-review/` root must be
# discovered and its scripts passed to shellcheck. Builds a base whose ONLY
# shell script lives under that root and asserts the stub shellcheck received
# it — proves the new root is gated, not just declared.
t_gates_codex_review_root() {
  local d; d=$(mktemp -d)
  mkdir -p "$d/base/.github/codex-review" "$d/bin"
  printf '#!/usr/bin/env bash\necho hi\n' > "$d/base/.github/codex-review/driver.sh"
  cat > "$d/bin/shellcheck" <<'STUB'
#!/usr/bin/env bash
echo "shellcheck $*" >> "$STUB_LOG"
exit 0
STUB
  cat > "$d/bin/pyright" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod +x "$d/bin/shellcheck" "$d/bin/pyright"
  local log="$d/stub.log"; : > "$log"
  # Capture the exit code explicitly (never `|| true` — rules/error-handling.md
  # Shell Error Handling): both stub engines exit 0, so a clean run is exit 0.
  local rc=0
  PATH="$d/bin:$PATH" STUB_LOG="$log" bash "$RUNNER" "$d/base" >/dev/null 2>&1 || rc=$?
  # Fixed-string match (`-F`): the '.' in 'driver.sh' is a regex any-char
  # without it, which would let an unintended filename satisfy the assertion.
  local hit; hit=$(grep -cF 'codex-review/driver.sh' "$log")
  rm -rf "$d"
  assert_eq "runner exits clean with only a codex-review script" "0" "$rc" || return 1
  assert_eq "codex-review script passed to shellcheck" "1" "$hit"
}

t_missing_base_exits_two() {
  local d; d=$(make_fixture)
  RC=0
  PATH="$d/bin:$PATH" bash "$RUNNER" "$d/nonexistent" >/dev/null 2>&1 || RC=$?
  rm -rf "$d"
  assert_eq "exit" "2" "$RC"
}

t_no_scripts_exits_two() {
  local d; d=$(mktemp -d)
  mkdir -p "$d/base/scripts" "$d/bin"   # scripts/ exists but holds no .sh
  cat > "$d/bin/shellcheck" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  cat > "$d/bin/pyright" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
  chmod +x "$d/bin/shellcheck" "$d/bin/pyright"
  RC=0
  PATH="$d/bin:$PATH" bash "$RUNNER" "$d/base" >/dev/null 2>&1 || RC=$?
  rm -rf "$d"
  assert_eq "exit" "2" "$RC"
}

t_missing_engine_exits_two() {
  local d; d=$(make_fixture)
  # Point PATH at a bin/ that has shellcheck but no pyright.
  rm -f "$d/bin/pyright"
  RC=0
  PATH="$d/bin:/usr/bin:/bin" bash "$RUNNER" "$d/base" >/dev/null 2>&1 || RC=$?
  rm -rf "$d"
  assert_eq "exit" "2" "$RC"
}

echo "== run-diagnostics.sh tests =="
run "both engines clean -> exit 0"                       t_both_clean_exits_zero
run "shellcheck finding -> exit 1"                       t_shellcheck_finding_exits_one
run "pyright finding -> exit 1"                          t_pyright_finding_exits_one
run "both engines find -> exit 1"                        t_both_findings_exit_one
run "gates the .github/codex-review/ root (#199)"        t_gates_codex_review_root
run "pyright runs even when shellcheck fails"            t_pyright_runs_even_when_shellcheck_fails
run "missing base dir -> exit 2"                         t_missing_base_exits_two
run "no shell scripts found -> exit 2"                   t_no_scripts_exits_two
run "engine not installed -> exit 2"                     t_missing_engine_exits_two

echo "== summary: ${PASS_COUNT} passed, ${FAIL_COUNT} failed =="
[[ "$FAIL_COUNT" -eq 0 ]]
