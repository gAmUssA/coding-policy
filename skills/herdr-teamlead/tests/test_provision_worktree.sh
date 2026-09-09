#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/provision-worktree.sh.
#
# Real local git repos, driven offline: a bare "origin" plus a shared checkout
# cloned from it, per scenario, so the cases share no state and run in any
# order (rules/testing-standards.md Independence). WORKTREE_ROOT points at the
# temp dir so nothing lands in the operator's real ~/.worktrees.
#
# The harness drops `set -e` to aggregate results, so every fixture-setup
# command is checked explicitly and aborts with a fatal diagnostic on failure
# (rules/error-handling.md aggregate-reporting carve-out).
#
# Covers:
#   1. Create           -> a new branch cut from the base ref, state created.
#   2. Attach local     -> an existing local branch is checked out, not recut.
#   3. Attach remote    -> an origin branch is tracked.
#   4. Idempotent       -> a re-run on the same path/branch is a no-op.
#   5. Wrong branch     -> a path holding another branch is exit 2.
#   6. Occupied path    -> a non-worktree path is exit 2, left alone.
#   7. Bad branch name  -> exit 1 per ci-safety naming.
#   8. Outside the root -> exit 1, nothing created.
#   9. Not a repo       -> exit 1.
#  10. Usage            -> exit 1 with a usage line.
#  11. Foreign worktree -> a work tree of ANOTHER repo on the same branch name
#                          is refused, naming both git dirs. Same branch name
#                          is not identity; the shared object store is.
#
# Run: bash skills/herdr-teamlead/tests/test_provision_worktree.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_repo() { # <prefix> -> sets SHARED
  local prefix="$1"
  local bare="$TMP/${prefix}.git"
  local seed="$TMP/${prefix}-seed"
  git init -q --bare -b main "$bare"            || die "git init --bare failed"
  git clone -q "$bare" "$seed" 2>/dev/null      || die "git clone failed"
  printf 'x\n' > "$seed/f"                      || die "seed write failed"
  git -C "$seed" add f                          || die "git add failed"
  git -C "$seed" commit -q -m c1                || die "git commit failed"
  git -C "$seed" push -q origin main            || die "git push failed"
  SHARED="$TMP/${prefix}-shared"
  git clone -q "$bare" "$SHARED" 2>/dev/null    || die "git clone (shared) failed"
  git -C "$SHARED" remote set-head origin --auto >/dev/null 2>&1 \
    || die "git remote set-head failed"
  SEED="$seed"
}

run() { # <shared> <branch> <path> [base]
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

main() {
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/provision-worktree.sh"
  [[ -f "$SCRIPT" && -r "$SCRIPT" ]] || die "provision-worktree.sh not found at $SCRIPT"
  command -v jq  >/dev/null 2>&1 || die "jq required for these tests"
  command -v git >/dev/null 2>&1 || die "git required for these tests"
  TMP="$(mktemp -d -t teamlead-provision-test.XXXXXX)" || die "mktemp failed"
  trap cleanup EXIT
  export HOME="$TMP/home"; mkdir -p "$HOME" || die "could not create isolated HOME"
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  ROOT="$TMP/worktrees"
  FAIL=0; PASS=0; RUN_SEQ=0

  # 1. A fresh branch.
  mk_repo r1
  run "$SHARED" "feat/thing" "$ROOT/r1-dev"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.state == "created" and .branch == "feat/thing"' >/dev/null 2>&1 \
     && [[ "$(git -C "$ROOT/r1-dev" rev-parse --abbrev-ref HEAD)" == "feat/thing" ]]; then
    pass; else fail "create: got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 2. A branch that already exists locally is attached, never recut.
  mk_repo r2
  git -C "$SHARED" branch feat/existing >/dev/null 2>&1 || die "git branch failed"
  run "$SHARED" "feat/existing" "$ROOT/r2-dev"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.state == "attached"' >/dev/null 2>&1; then
    pass; else fail "attach local: got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 3. A branch that exists only on origin is tracked.
  mk_repo r3
  git -C "$SEED" checkout -q -b fix-42                || die "git checkout -b failed"
  git -C "$SEED" commit -q --allow-empty -m c2        || die "git commit failed"
  git -C "$SEED" push -q origin fix-42                || die "git push failed"
  git -C "$SHARED" fetch -q origin                    || die "git fetch failed"
  run "$SHARED" "fix-42" "$ROOT/r3-test"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.state == "attached"' >/dev/null 2>&1 \
     && [[ "$(git -C "$ROOT/r3-test" rev-parse --abbrev-ref HEAD)" == "fix-42" ]]; then
    pass; else fail "attach remote: got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 4. Re-running the same provision is a no-op, not an error.
  run "$SHARED" "fix-42" "$ROOT/r3-test"
  if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.state == "already-provisioned"' >/dev/null 2>&1; then
    pass; else fail "idempotent: got RC=$RC OUT=$OUT ERR=$ERRTEXT"; fi

  # 5. A path already holding a DIFFERENT branch is never repurposed.
  run "$SHARED" "feat/other" "$ROOT/r3-test"
  if [[ $RC -eq 2 ]] && printf '%s' "$ERRTEXT" | grep -q "fix-42"; then
    pass; else fail "wrong branch: expected exit 2 naming the branch, got RC=$RC ERR=$ERRTEXT"; fi

  # 6. A path that is not a work tree at all is left alone.
  mkdir -p "$ROOT/occupied" || die "could not create the occupied path"
  printf 'someone else\n' > "$ROOT/occupied/notes.md" || die "could not write the occupied file"
  run "$SHARED" "feat/thing" "$ROOT/occupied"
  if [[ $RC -eq 2 ]] && [[ -f "$ROOT/occupied/notes.md" ]]; then
    pass; else fail "occupied path: expected exit 2 leaving it alone, got RC=$RC"; fi

  # 7. Branch naming per rules/ci-safety.md.
  mk_repo r7
  run "$SHARED" "Feature_Branch" "$ROOT/r7-dev"
  if [[ $RC -eq 1 && -z "$OUT" ]] && printf '%s' "$ERRTEXT" | grep -q "ci-safety"; then
    pass; else fail "bad branch name: expected exit 1 citing the rule, got RC=$RC ERR=$ERRTEXT"; fi

  # 8. A worktree belongs under the worktree root, never beside a project.
  run "$SHARED" "feat/thing" "$TMP/elsewhere/dev"
  if [[ $RC -eq 1 && -z "$OUT" ]] && [[ ! -d "$TMP/elsewhere/dev/.git" ]]; then
    pass; else fail "outside root: expected exit 1 with nothing created, got RC=$RC"; fi

  # 9. A shared checkout that is not a repo.
  mkdir -p "$TMP/notrepo" || die "could not create notrepo"
  run "$TMP/notrepo" "feat/thing" "$ROOT/x"
  if [[ $RC -eq 1 && -z "$OUT" ]]; then
    pass; else fail "not a repo: expected exit 1, got RC=$RC"; fi

  # 10. Usage.
  OUT="$(env WORKTREE_ROOT="$ROOT" bash "$SCRIPT" 2>"$TMP/e10")"; RC=$?
  if [[ $RC -eq 1 && -z "$OUT" ]] && grep -q "usage:" "$TMP/e10"; then
    pass; else fail "usage: expected exit 1 with a usage line, got RC=$RC"; fi

  # 11. Two unrelated repos both have a `feat/thing`. Accepting one as the
  #     other's provisioned worktree would hand a worker somebody else's tree,
  #     which is why identity is the common git dir and not the branch name.
  mk_repo r11                       # SHARED = the checkout we provision from
  local ours="$SHARED"
  mk_repo r11b                      # a second, unrelated repo
  local theirs="$SHARED"
  # Their worktree, on the same branch name, at the path we are about to ask
  # for. Created by THEIR repo, so its common dir differs from ours.
  git -C "$theirs" worktree add -q -b "feat/thing" "$ROOT/r11-dev" >/dev/null 2>&1 \
    || die "could not create the foreign worktree"
  run "$ours" "feat/thing" "$ROOT/r11-dev"
  if [[ $RC -eq 2 && -z "$OUT" ]] \
     && printf '%s' "$ERRTEXT" | grep -q "DIFFERENT repository" \
     && printf '%s' "$ERRTEXT" | grep -q "$(basename "$theirs")"; then
    pass; else fail "foreign worktree: expected exit 2 naming both git dirs, got RC=$RC ERR=$ERRTEXT"; fi
  # And it is left exactly as it was.
  if [[ "$(git -C "$ROOT/r11-dev" rev-parse --abbrev-ref HEAD)" == "feat/thing" ]]; then
    pass; else fail "foreign worktree: the other repo's work tree was disturbed"; fi

  # 11b. Our own worktree on that branch still reads as already-provisioned,
  #      so the identity check did not simply forbid every re-run.
  run "$ours" "feat/other" "$ROOT/r11-ours"
  if [[ $RC -eq 0 ]]; then
    run "$ours" "feat/other" "$ROOT/r11-ours"
    if [[ $RC -eq 0 ]] && printf '%s' "$OUT" | jq -e '.state == "already-provisioned"' >/dev/null 2>&1; then
      pass; else fail "own worktree: expected already-provisioned, got RC=$RC OUT=$OUT"; fi
  else
    fail "own worktree: could not provision it in the first place (RC=$RC ERR=$ERRTEXT)"
  fi

  echo "─────────────────────────────────────────────" >&2
  if [[ $FAIL -gt 0 ]]; then echo "FAILED: ${FAIL} failed, ${PASS} passed" >&2; exit 1; fi
  echo "PASSED: all ${PASS} checks" >&2
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
