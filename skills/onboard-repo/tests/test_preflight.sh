#!/usr/bin/env bash
# Outcome-based tests for preflight.sh. Each case runs in a throwaway git repo
# with a fake `gh` first on PATH, a fixture Codex credential (CODEX_AUTH_FILE)
# and a fixture plugin mount (PLUGIN_MOUNT) — no network, no shared state.
#
# Covers:
#   1. all green (install)         -> ok, exit 0, no failures.
#   2. gh unauthenticated          -> gh-authenticated failure, exit 1.
#   3. codex credential missing    -> codex-auth failure.
#   4. codex credential not chatgpt -> codex-auth failure.
#   5. templates missing           -> templates-present failure names tessl install.
#   6. install with a target present -> targets-absent failure (says --override).
#   7. override with dirty target  -> targets-clean failure.
#   8. override with clean target  -> ok.
#   9. not a git repo              -> in-git-worktree failure.
#  10. bad argument                -> exit 2.
#
# Run: bash skills/onboard-repo/tests/test_preflight.sh
set -uo pipefail

# Isolate git from the operator's global/system config (a global gitignore
# that ignores .github/ would make an untracked target read as clean).
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="${SKILL_DIR}/preflight.sh"
[[ -f "$SCRIPT" ]] || { echo "fatal: preflight.sh not found at $SCRIPT" >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo "fatal: jq required" >&2; exit 2; }

PASS=0; FAIL=0
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }

mkrepo() { # <dir>
  git -c init.defaultBranch=main init -q "$1" \
    && git -C "$1" -c user.email=t@t -c user.name=t commit --allow-empty -q -m init \
    && git -C "$1" remote add origin https://github.com/testowner/testrepo.git
}
mkmount() { # <dir> : fixture plugin mount with every template
  local t="$1/skills/onboard-repo/templates"; mkdir -p "$t" \
    && for f in review-codex.yml.md copilot-instructions.md prompt.md schema.json post-review.sh mask-secrets.sh assert-no-secret-leak.sh; do
         printf '# %s\n' "$f" > "$t/$f"; done
}
# run <repo> [extra env...] [-- args...] -> OUT, RC
run() {
  local repo="$1"; shift
  OUT="$(cd "$repo" && env "PATH=$STUBBIN:$PATH" CODEX_AUTH_FILE="$AUTH_OK" PLUGIN_MOUNT="$MOUNT" "$@" bash "$SCRIPT" 2>/dev/null)"; RC=$?
}
has_failure() { jq -e --arg c "$1" '.failures[] | select(.check == $c)' <<<"$OUT" >/dev/null 2>&1; }

main() {
  TMP="$(mktemp -d)" || { echo "fatal: mktemp" >&2; exit 2; }
  trap cleanup EXIT

  STUBBIN="$TMP/bin"; mkdir -p "$STUBBIN"
  cat > "$STUBBIN/gh" <<'STUB'
#!/usr/bin/env bash
if [[ "$1 $2" == "auth status" ]]; then [[ -n "${STUB_GH_UNAUTH:-}" ]] && exit 1; exit 0; fi
exit 0
STUB
  chmod +x "$STUBBIN/gh"
  AUTH_OK="$TMP/auth-ok.json"; printf '{"auth_mode":"chatgpt","has_refresh_token":true,"tokens":{}}\n' > "$AUTH_OK"
  AUTH_API="$TMP/auth-api.json"; printf '{"auth_mode":"apikey","has_refresh_token":false}\n' > "$AUTH_API"
  MOUNT="$TMP/mount"; mkmount "$MOUNT"

  # 1. all green
  mkrepo "$TMP/r1"; run "$TMP/r1"
  if [[ $RC -eq 0 ]] && jq -e '.ok == true and (.failures | length == 0) and .override == false' <<<"$OUT" >/dev/null 2>&1; then pass; else fail "green: RC=$RC OUT=$OUT"; fi

  # 2. gh unauthenticated
  mkrepo "$TMP/r2"; run "$TMP/r2" STUB_GH_UNAUTH=1
  if [[ $RC -eq 1 ]] && has_failure gh-authenticated; then pass; else fail "gh unauth: RC=$RC OUT=$OUT"; fi

  # 3. codex credential missing
  mkrepo "$TMP/r3"; run "$TMP/r3" CODEX_AUTH_FILE="$TMP/nope.json"
  if [[ $RC -eq 1 ]] && has_failure codex-auth && jq -e '.failures[] | select(.check=="codex-auth") | .reason | test("codex login")' <<<"$OUT" >/dev/null; then pass; else fail "codex missing: RC=$RC OUT=$OUT"; fi

  # 4. codex credential wrong mode
  mkrepo "$TMP/r4"; run "$TMP/r4" CODEX_AUTH_FILE="$AUTH_API"
  if [[ $RC -eq 1 ]] && has_failure codex-auth; then pass; else fail "codex apikey: RC=$RC OUT=$OUT"; fi

  # 5. templates missing
  mkrepo "$TMP/r5"; run "$TMP/r5" PLUGIN_MOUNT="$TMP/empty-mount"
  if [[ $RC -eq 1 ]] && has_failure templates-present && grep -q 'tessl install gamussa/coding-policy' <<<"$OUT"; then pass; else fail "templates: RC=$RC OUT=$OUT"; fi

  # 6. install mode, target present
  mkrepo "$TMP/r6"; mkdir -p "$TMP/r6/.github/workflows"; printf 'x\n' > "$TMP/r6/.github/workflows/review-codex.yml"
  run "$TMP/r6"
  if [[ $RC -eq 1 ]] && has_failure targets-absent && grep -q -- '--override' <<<"$OUT"; then pass; else fail "target present: RC=$RC OUT=$OUT"; fi

  # 7. override, dirty target (untracked counts as dirty)
  OUT="$(cd "$TMP/r6" && env "PATH=$STUBBIN:$PATH" CODEX_AUTH_FILE="$AUTH_OK" PLUGIN_MOUNT="$MOUNT" bash "$SCRIPT" --override 2>/dev/null)"; RC=$?
  if [[ $RC -eq 1 ]] && has_failure targets-clean; then pass; else fail "override dirty: RC=$RC OUT=$OUT"; fi

  # 8. override, committed target -> ok
  git -C "$TMP/r6" add -A && git -C "$TMP/r6" -c user.email=t@t -c user.name=t commit -q -m add
  OUT="$(cd "$TMP/r6" && env "PATH=$STUBBIN:$PATH" CODEX_AUTH_FILE="$AUTH_OK" PLUGIN_MOUNT="$MOUNT" bash "$SCRIPT" --override 2>/dev/null)"; RC=$?
  if [[ $RC -eq 0 ]] && jq -e '.ok == true and .override == true' <<<"$OUT" >/dev/null 2>&1; then pass; else fail "override clean: RC=$RC OUT=$OUT"; fi

  # 9. not a git repo
  mkdir -p "$TMP/r9"; run "$TMP/r9"
  if [[ $RC -eq 1 ]] && has_failure in-git-worktree; then pass; else fail "not repo: RC=$RC OUT=$OUT"; fi

  # 10. bad argument
  mkrepo "$TMP/r10"; OUT="$(cd "$TMP/r10" && bash "$SCRIPT" --bogus 2>/dev/null)"; RC=$?
  if [[ $RC -eq 2 ]]; then pass; else fail "bad arg: RC=$RC"; fi

  echo "─────────────────────────────────────────────"
  if (( FAIL > 0 )); then echo "FAILED: ${FAIL} of $((PASS+FAIL)) checks" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks"
}

main "$@"
