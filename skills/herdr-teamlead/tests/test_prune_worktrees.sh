#!/usr/bin/env bash
# Outcome-based tests for skills/herdr-teamlead/prune-worktrees.sh.
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
#   1. Merged + clean      -> worktree removed, branch deleted.
#   2. Merged via origin   -> a branch whose commit landed on origin's default
#                             branch is removed after the fetch.
#   3. Unmerged            -> kept, reason unmerged; branch survives.
#   4. Dirty (untracked)   -> kept, reason dirty.
#   5. Dirty (modified)    -> kept, reason dirty.
#   6. Detached            -> kept, reason detached.
#   7. Locked              -> kept, reason locked.
#   8. Outside the root    -> kept, reason outside-root, never removed.
#   9. Shared checkout     -> never listed, never removed; default branch kept.
#  10. Branch, no worktree -> merged one deleted, unmerged one kept.
#  11. Dry run             -> same decisions reported, nothing changes.
#  12. Stale metadata      -> a hand-deleted worktree dir is pruned.
#  13. Foreign worktree    -> a directory of ANOTHER repo under the root is
#                             untouched.
#  14. Usage / not a repo  -> exit 1, no JSON.
#  15. Fetch failure       -> exit 1, no JSON; nothing judged from stale refs.
#  16. Tool failure        -> a merge-base error is a failed row on stdout and
#                             stderr, exit 2, never a kept 'unmerged'.
#  17. Dry-run metadata    -> stale metadata and origin/HEAD survive a dry run;
#                             the remote default is still resolved.
#  18. Unenterable         -> a worktree the run cannot cd into is a failed
#                             row, exit 2 (skipped as root, who can enter anything).
#  19. Shadowing           -> a tag named like a branch, or a local branch
#                             named origin/main, cannot stand in for either operand.
#  20. Untraversable parent-> absence is not confirmed; failed row, metadata
#                             kept, exit 2 (skipped as root).
#  21. Raced branch        -> a tip that moved after its ancestry check is kept.
#  22. Half-done removal   -> a removal is reported even when its branch
#                             deletion then fails.
#  23. Deferred prunable   -> a prunable branch survives a skipped prune.
#  24. Newline path        -> a record is not split by a newline in the path.
#  25. Branch config       -> a deleted branch's branch.<name> config goes too.
#  26. Locked and gone     -> git keeps its metadata, so its branch is kept too.
#  27. No -z               -> a git without `-z` decides nothing at all.
#  28. Dry-run deferral    -> a preview defers what the live run would defer.
#  29. Config sibling      -> branch.<name>.<key> of a LONGER branch name is
#                             not read as this branch's config.
#  30. Claimed branch      -> a branch a worktree holds is kept, not deleted.
#  31. Claimed mid-delete  -> a worktree claiming it inside the deletion's own
#                             window gets the branch back.
#  32. Newline parent      -> absence is confirmed through a parent whose own
#                             name ends in a newline.
#  33. Unreadable recheck   -> a failed post-deletion occupancy read is a
#                             failure, never an unoccupied answer.
#
# Run: bash skills/herdr-teamlead/tests/test_prune_worktrees.sh
set -uo pipefail

die() { echo "fatal: $*" >&2; exit 2; }
cleanup() { [[ -n "${TMP:-}" ]] && ! rm -rf "$TMP" && echo "warn: could not remove $TMP" >&2; return 0; }
pass() { PASS=$((PASS+1)); }
fail() { FAIL=$((FAIL+1)); echo "  ✗ FAIL: $1" >&2; }

mk_repo() { # <prefix> -> sets SHARED, SEED, BARE
  local prefix="$1"
  BARE="$TMP/${prefix}.git"
  SEED="$TMP/${prefix}-seed"
  git init -q --bare -b main "$BARE"            || die "git init --bare failed"
  git clone -q "$BARE" "$SEED" 2>/dev/null      || die "git clone failed"
  printf 'x\n' > "$SEED/f"                      || die "seed write failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t add f  || die "git add failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t commit -q -m c1 || die "git commit failed"
  git -C "$SEED" push -q origin main            || die "git push failed"
  SHARED="$TMP/${prefix}-shared"
  git clone -q "$BARE" "$SHARED" 2>/dev/null    || die "git clone (shared) failed"
  git -C "$SHARED" remote set-head origin --auto >/dev/null 2>&1 \
    || die "git remote set-head failed"
}

add_wt() { # <shared> <branch> <path>  (cut at main)
  git -C "$1" worktree add -q -b "$2" "$3" origin/main 2>/dev/null || die "worktree add $2 failed"
}

commit_in() { # <worktree> <file>
  printf 'y\n' > "$1/$2" || die "write $2 failed"
  git -C "$1" -c user.name=t -c user.email=t@t add "$2" || die "git add in $1 failed"
  git -C "$1" -c user.name=t -c user.email=t@t commit -q -m "c-$2" || die "git commit in $1 failed"
}

run() { # <args...>
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" bash "$SCRIPT" "$@" 2>"$TMP/err.$RUN_SEQ")"
  RC=$?
  ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
}

# jq-free field readers over $OUT.
removed_paths() { python3 -c 'import json,sys; print("\n".join(r["path"] for r in json.load(sys.stdin)["worktrees_removed"]))' <<<"$OUT"; }
kept_reason() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["reason"] for r in d["worktrees_kept"] if r["path"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
branches_deleted() { python3 -c 'import json,sys; print("\n".join(json.load(sys.stdin)["branches_deleted"]))' <<<"$OUT"; }
branch_kept_reason() { python3 -c 'import json,sys; d=json.load(sys.stdin); print(next((r["reason"] for r in d["branches_kept"] if r["branch"]==sys.argv[1]), ""))' "$1" <<<"$OUT"; }
field() { python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1" <<<"$OUT"; }
mentions_path() { python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if any(r["path"]==sys.argv[1] for r in d["worktrees_kept"]+d["worktrees_removed"]) else 1)' "$1" <<<"$OUT"; }
has_branch() { # <shared> <branch> -> 0 present, 1 absent; a git error aborts the harness
  local rc=0
  git -C "$1" show-ref --verify --quiet "refs/heads/$2" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "git show-ref failed (exit $rc) for $2 in $1" ;; esac
}
listed() { # <shared> <path>  -> 0 listed, 1 not listed; a tool failure aborts the harness
  local inventory rc=0
  inventory="$(git -C "$1" worktree list --porcelain)" || die "git worktree list failed in $1"
  grep -qx "worktree $2" <<<"$inventory" || rc=$?
  case "$rc" in 0) return 0 ;; 1) return 1 ;; *) die "grep failed (exit $rc) reading the worktree inventory" ;; esac
}

main() {
  PASS=0; FAIL=0; RUN_SEQ=0
  SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/prune-worktrees.sh"
  [[ -f "$SCRIPT" ]] || die "script not found: $SCRIPT"
  TMP="$(mktemp -d)" || die "mktemp failed"
  # The script reports physical paths; macOS mktemp hands out a symlinked /var.
  TMP="$(cd "$TMP" && pwd -P)" || die "resolve TMP failed"
  trap cleanup EXIT
  ROOT="$TMP/worktrees"
  mkdir -p "$ROOT" || die "mkdir root failed"

  # --- 1, 3, 4, 5, 6, 7, 9, 10 share one repo: one run decides them all.
  mk_repo one
  add_wt "$SHARED" review/merged "$ROOT/one-merged"
  add_wt "$SHARED" test/unmerged "$ROOT/one-unmerged"; commit_in "$ROOT/one-unmerged" u
  add_wt "$SHARED" test/untracked "$ROOT/one-untracked"; printf 'z\n' > "$ROOT/one-untracked/scratch" || die "write failed"
  git -C "$SHARED" config status.showUntrackedFiles no || die "config failed"
  add_wt "$SHARED" test/modified "$ROOT/one-modified"; printf 'changed\n' > "$ROOT/one-modified/f" || die "write failed"
  git -C "$SHARED" worktree add -q --detach "$ROOT/one-detached" origin/main 2>/dev/null || die "detached add failed"
  add_wt "$SHARED" test/locked "$ROOT/one-locked"; git -C "$SHARED" worktree lock "$ROOT/one-locked" || die "lock failed"
  git -C "$SHARED" branch --no-track merged-no-wt origin/main || die "branch failed"
  git -C "$SHARED" branch --no-track unmerged-no-wt origin/main || die "branch failed"
  git -C "$SHARED" worktree add -q "$TMP/one-scratch" unmerged-no-wt 2>/dev/null || die "scratch add failed"
  commit_in "$TMP/one-scratch" w
  git -C "$SHARED" worktree remove "$TMP/one-scratch" || die "scratch remove failed"

  run "$SHARED"
  echo "1. merged + clean worktree is removed and its branch deleted"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/one-merged"* ]] && [[ ! -e "$ROOT/one-merged" ]] && ! has_branch "$SHARED" review/merged; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  echo "3. unmerged worktree is kept with reason unmerged"
  if [[ "$(kept_reason "$ROOT/one-unmerged")" == unmerged ]] && [[ -d "$ROOT/one-unmerged" ]] && has_branch "$SHARED" test/unmerged; then pass; else fail "out=$OUT"; fi
  echo "4. untracked file keeps the worktree as dirty, even with status.showUntrackedFiles=no"
  if [[ "$(kept_reason "$ROOT/one-untracked")" == dirty ]] && [[ -f "$ROOT/one-untracked/scratch" ]]; then pass; else fail "out=$OUT"; fi
  echo "5. modified file keeps the worktree as dirty"
  if [[ "$(kept_reason "$ROOT/one-modified")" == dirty ]] && [[ -d "$ROOT/one-modified" ]]; then pass; else fail "out=$OUT"; fi
  echo "6. detached worktree is kept"
  if [[ "$(kept_reason "$ROOT/one-detached")" == detached ]] && [[ -d "$ROOT/one-detached" ]]; then pass; else fail "out=$OUT"; fi
  echo "7. locked worktree is kept"
  if [[ "$(kept_reason "$ROOT/one-locked")" == locked ]] && [[ -d "$ROOT/one-locked" ]]; then pass; else fail "out=$OUT"; fi
  echo "9. the shared checkout is never listed and the default branch survives"
  if ! mentions_path "$SHARED" && has_branch "$SHARED" main && [[ "$(field default_branch)" == main ]]; then pass; else fail "out=$OUT"; fi
  echo "10. merged branch without a worktree is deleted; unmerged one is kept"
  if [[ "$(branches_deleted)" == *merged-no-wt* ]] && ! has_branch "$SHARED" merged-no-wt && [[ "$(branch_kept_reason unmerged-no-wt)" == unmerged ]] && has_branch "$SHARED" unmerged-no-wt; then pass; else fail "out=$OUT"; fi

  # --- 2. merged via origin: commit on a branch, land it on origin main, prune.
  mk_repo two
  add_wt "$SHARED" feat/landed "$ROOT/two-landed"; commit_in "$ROOT/two-landed" landed
  git -C "$ROOT/two-landed" push -q origin feat/landed || die "push failed"
  git -C "$SEED" fetch -q origin || die "seed fetch failed"
  git -C "$SEED" -c user.name=t -c user.email=t@t merge -q --no-ff origin/feat/landed -m merge || die "seed merge failed"
  git -C "$SEED" push -q origin main || die "seed push failed"
  run "$SHARED"
  echo "2. a branch landed on origin's default branch is removed after the fetch"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/two-landed"* ]] && ! has_branch "$SHARED" feat/landed; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 8. outside the root.
  mk_repo eight
  git -C "$SHARED" worktree add -q -b review/outside "$TMP/eight-outside" origin/main 2>/dev/null || die "outside add failed"
  run "$SHARED"
  echo "8. a worktree outside the root is kept and reported"
  if (( RC == 0 )) && [[ "$(kept_reason "$TMP/eight-outside")" == outside-root ]] && [[ -d "$TMP/eight-outside" ]] && has_branch "$SHARED" review/outside; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 11. dry run.
  mk_repo eleven
  add_wt "$SHARED" review/dry "$ROOT/eleven-dry"
  git -C "$SHARED" branch --no-track dry-no-wt origin/main || die "branch failed"
  run "$SHARED" --dry-run
  echo "11. dry run reports the decisions and changes nothing"
  if (( RC == 0 )) && [[ "$(field dry_run)" == True ]] && [[ "$(removed_paths)" == *"$ROOT/eleven-dry"* ]] && [[ "$(branches_deleted)" == *dry-no-wt* ]] && [[ -d "$ROOT/eleven-dry" ]] && has_branch "$SHARED" review/dry && has_branch "$SHARED" dry-no-wt; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 12. stale metadata.
  mk_repo twelve
  add_wt "$SHARED" review/gone "$ROOT/twelve-gone"
  rm -rf "$ROOT/twelve-gone" || die "rm failed"
  run "$SHARED"
  echo "12. a hand-deleted worktree's metadata is pruned and its merged branch deleted in the same run"
  if (( RC == 0 )) && ! listed "$SHARED" "$ROOT/twelve-gone" && [[ "$(kept_reason "$ROOT/twelve-gone")" == prunable ]] && [[ "$(branches_deleted)" == *review/gone* ]] && ! has_branch "$SHARED" review/gone; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 13. foreign worktree under the root.
  mk_repo thirteen
  local foreign_shared="$SHARED"
  mk_repo other
  add_wt "$SHARED" review/other "$ROOT/thirteen-other"
  run "$foreign_shared"
  echo "13. another repository's worktree under the root is untouched"
  if (( RC == 0 )) && [[ -d "$ROOT/thirteen-other" ]] && [[ "$OUT" != *thirteen-other* ]]; then pass; else fail "rc=$RC out=$OUT"; fi

  # --- 15. an unreachable origin is a precondition failure: nothing is judged from stale refs.
  mk_repo fifteen
  add_wt "$SHARED" review/stale "$ROOT/fifteen-stale"
  git -C "$SHARED" remote set-url origin "$TMP/nowhere.git" || die "set-url failed"
  run "$SHARED"
  echo "15. a failed fetch is exit 1 with no JSON and the merged worktree untouched"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"stale refs"* ]] && [[ -d "$ROOT/fifteen-stale" ]] && has_branch "$SHARED" review/stale; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 16. a merge-base tool failure is a failed row on stderr and stdout, exit 2, never "unmerged".
  mk_repo sixteen
  add_wt "$SHARED" review/broken "$ROOT/sixteen-broken"
  git -C "$SHARED" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/vanished || die "symbolic-ref failed"
  git -C "$SEED" push -q origin main:refs/heads/vanished || die "push vanished failed"
  git -C "$SEED" symbolic-ref HEAD refs/heads/vanished || die "seed symbolic-ref failed"
  git -C "$BARE" symbolic-ref HEAD refs/heads/vanished || die "bare HEAD failed"
  git -C "$SHARED" fetch -q origin || die "fetch failed"
  # origin's default is now `vanished`; a git shim corrupts its remote-tracking
  # ref the moment the script reaches merge-base, after the run's own fetch.
  mkdir -p "$TMP/shim" || die "mkdir shim failed"
  cat > "$TMP/shim/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in *merge-base*) printf 'not-a-sha\n' > "$SHARED/.git/refs/remotes/origin/vanished" ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim/git" || die "chmod shim failed"
  run_with_shim() { RUN_SEQ=$((RUN_SEQ+1)); OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"; }
  run_with_shim
  echo "16. a merge-base tool failure lands in failed, on stderr, exit 2, worktree untouched"
  if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*merge-base* ]] && [[ "$ERRTEXT" == *"merge-base failed"* ]] && [[ "$(kept_reason "$ROOT/sixteen-broken")" == "" ]] && [[ -d "$ROOT/sixteen-broken" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 17. dry run leaves stale metadata and remote-tracking refs in place.
  mk_repo seventeen
  add_wt "$SHARED" review/preview "$ROOT/seventeen-preview"
  rm -rf "$ROOT/seventeen-preview" || die "rm failed"
  git -C "$SHARED" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/elsewhere || die "symbolic-ref failed"
  head_before="$(cat "$SHARED/.git/refs/remotes/origin/HEAD")" || die "read HEAD failed"
  run "$SHARED" --dry-run
  echo "17. dry run prunes no metadata, rewrites no origin/HEAD, and still finds the remote default"
  if (( RC == 0 )) && listed "$SHARED" "$ROOT/seventeen-preview" && [[ "$(cat "$SHARED/.git/refs/remotes/origin/HEAD")" == "$head_before" ]] && [[ "$(field default_branch)" == main ]] && [[ "$(kept_reason "$ROOT/seventeen-preview")" == prunable ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 18. an unenterable worktree is a failed row, exit 2, never prunable.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo eighteen
    add_wt "$SHARED" review/sealed "$ROOT/eighteen-sealed"
    chmod 000 "$ROOT/eighteen-sealed" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/eighteen-sealed" || die "chmod restore failed"
    echo "18. a worktree that cannot be entered is a failed row on stderr, exit 2"
    if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*"cannot enter"* ]] && [[ "$ERRTEXT" == *"cannot enter"* ]] && [[ "$ERRTEXT" == *"skipping"* ]] && has_branch "$SHARED" review/sealed && listed "$SHARED" "$ROOT/eighteen-sealed"; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 19. a merged tag named like an unmerged branch must not shadow it.
  mk_repo nineteen
  add_wt "$SHARED" review/shadow "$ROOT/nineteen-shadow"; commit_in "$ROOT/nineteen-shadow" s
  git -C "$SHARED" tag review/shadow origin/main || die "tag failed"
  git -C "$SHARED" branch --no-track origin/main origin/main 2>/dev/null || die "shadow branch failed"
  run "$SHARED"
  echo "19. ancestry is judged on fully qualified refs: a same-name tag or an origin/main local branch cannot shadow"
  if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/nineteen-shadow")" == unmerged ]] && [[ -d "$ROOT/nineteen-shadow" ]] && has_branch "$SHARED" review/shadow; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 20. an ancestor that denies traversal is not "gone".
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twenty
    mkdir -p "$ROOT/twenty-parent" || die "mkdir failed"
    add_wt "$SHARED" review/hidden "$ROOT/twenty-parent/hidden"
    chmod 000 "$ROOT/twenty-parent" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/twenty-parent" || die "chmod restore failed"
    echo "20. a worktree behind an untraversable parent is a failed row, metadata and branch kept, exit 2"
    if (( RC == 2 )) && [[ "$OUT" == *'"failed": [{'*"cannot confirm"* ]] && [[ "$ERRTEXT" == *"skipping"* ]] && has_branch "$SHARED" review/hidden && listed "$SHARED" "$ROOT/twenty-parent/hidden"; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 21. a branch that moves between its ancestry check and the deletion is kept.
  mk_repo twentyone
  add_wt "$SHARED" review/racing "$ROOT/twentyone-racing"
  # Advance origin/main so the shim has a second merged commit to move to.
  commit_in "$SEED" second
  git -C "$SEED" push -q origin main || die "push failed"
  git -C "$SHARED" fetch -q origin || die "fetch failed"
  mkdir -p "$TMP/shim21" || die "mkdir shim failed"
  cat > "$TMP/shim21/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
# Move the branch on the SECOND read of its tip — after the worktree is gone,
# so the move is allowed, and before the compare-and-delete reads it. The new
# tip is merged too, so only the guard can keep the branch. A move that fails
# breaks the fixture's premise: say so and stop rather than let the run pass.
case "\$*" in *"refs/heads/review/racing"*)
  if [[ -e "$TMP/shim21/seen" ]]; then
    # The move's own chatter must not reach stdout: the caller is capturing it
    # as the branch tip.
    if ! "$(command -v git)" -C "$SHARED" branch -f review/racing refs/remotes/origin/main >&2; then
      echo "shim21: fixture could not move review/racing" >&2
      exit 1
    fi
  else
    : > "$TMP/shim21/seen"
  fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim21/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim21:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "21. a branch that moved after its ancestry check is kept, its removal still reported, exit 2"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$ROOT/twentyone-racing"* ]] && [[ "$OUT" == *"moved after its ancestry check"* ]] && has_branch "$SHARED" review/racing; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 22. a worktree removal whose branch deletion fails still reports the removal.
  mk_repo twentytwo
  add_wt "$SHARED" review/halfway "$ROOT/twentytwo-halfway"
  mkdir -p "$TMP/shim22" || die "mkdir shim failed"
  cat > "$TMP/shim22/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in *"update-ref -d"*) echo "fatal: fixture refuses the deletion" >&2; exit 1 ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim22/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim22:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "22. the JSON reports the removal that happened even when the branch deletion fails"
  if (( RC == 2 )) && [[ "$(removed_paths)" == *"$ROOT/twentytwo-halfway"* ]] && [[ ! -e "$ROOT/twentytwo-halfway" ]] && [[ "$OUT" == *'"failed": [{'*"deleting"* ]] && [[ "$OUT" == *"fixture refuses the deletion"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 23. a prunable branch is deferred when the metadata prune is skipped.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twentythree
    add_wt "$SHARED" review/vanished "$ROOT/twentythree-vanished"
    rm -rf "$ROOT/twentythree-vanished" || die "rm failed"
    add_wt "$SHARED" review/sealed23 "$ROOT/twentythree-sealed"
    chmod 000 "$ROOT/twentythree-sealed" || die "chmod failed"
    run "$SHARED"
    chmod 755 "$ROOT/twentythree-sealed" || die "chmod restore failed"
    echo "23. a prunable branch is not deleted while its metadata survives a skipped prune"
    if (( RC == 2 )) && [[ "$(kept_reason "$ROOT/twentythree-vanished")" == prunable ]] && [[ "$(branches_deleted)" != *review/vanished* ]] && has_branch "$SHARED" review/vanished; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 24. a worktree path holding a newline is one field, not two.
  mk_repo twentyfour
  newline_path="$ROOT/twentyfour-$(printf 'a\nb')"
  git -C "$SHARED" worktree add -q -b review/newline "$newline_path" origin/main \
    || die "fixture could not create a worktree at a newline-bearing path"
  run "$SHARED"
  echo "24. a newline in a worktree path does not split its record"
  if (( RC == 0 )) && [[ "$OUT" != *'"path": "'"$ROOT"'/twentyfour-a"'* ]] && ! has_branch "$SHARED" review/newline; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 25. a tracked branch's config goes with it.
  mk_repo twentyfive
  git -C "$SHARED" worktree add -q --track -b review/tracked "$ROOT/twentyfive-tracked" origin/main 2>/dev/null \
    || die "fixture could not create a tracking worktree"
  git -C "$SHARED" config --get-regexp '^branch\.review/tracked\.' >/dev/null || die "fixture branch has no tracking config"
  run "$SHARED"
  echo "25. a deleted branch leaves no stale branch.<name> config behind"
  cfg_rc=0
  git -C "$SHARED" config --get-regexp '^branch\.review/tracked\.' >/dev/null 2>"$TMP/cfg.err" || cfg_rc=$?
  (( cfg_rc == 0 || cfg_rc == 1 )) || die "git config failed (exit $cfg_rc): $(cat "$TMP/cfg.err")"
  if (( RC == 0 )) && [[ "$(removed_paths)" == *"$ROOT/twentyfive-tracked"* ]] && (( cfg_rc == 1 )); then pass; else fail "rc=$RC cfg_rc=$cfg_rc out=$OUT err=$ERRTEXT"; fi

  # --- 26. a locked entry whose directory is gone keeps its branch.
  mk_repo twentysix
  add_wt "$SHARED" review/lockedgone "$ROOT/twentysix-lockedgone"
  git -C "$SHARED" worktree lock "$ROOT/twentysix-lockedgone" || die "lock failed"
  rm -rf "$ROOT/twentysix-lockedgone" || die "rm failed"
  run "$SHARED"
  echo "26. a locked entry git's prune preserves does not release its branch"
  if (( RC == 0 )) && [[ "$(kept_reason "$ROOT/twentysix-lockedgone")" == locked ]] && [[ "$(branches_deleted)" != *review/lockedgone* ]] && has_branch "$SHARED" review/lockedgone; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 27. without -z there is no unambiguous inventory, so nothing is decided.
  # No newline path is needed: the line-oriented form cannot be trusted at all,
  # since a path whose tail reads as an attribute passes any scan (#410).
  mk_repo twentyseven
  add_wt "$SHARED" review/plain "$ROOT/twentyseven-plain"
  mkdir -p "$TMP/shim27" || die "mkdir shim failed"
  cat > "$TMP/shim27/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
# Stand in for a git older than 2.36, which has no -z on this subcommand.
case "\$*" in *"worktree list"*-z*) echo "error: unknown option z" >&2; exit 129 ;; esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim27/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim27:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "27. a git without -z refuses the inventory, deciding nothing"
  plain_dir=0; [[ -d "$ROOT/twentyseven-plain" ]] && plain_dir=1
  plain_branch=0; has_branch "$SHARED" review/plain && plain_branch=1
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *"cannot be listed unambiguously"* ]] && (( plain_dir )) && (( plain_branch )); then pass; else fail "rc=$RC dir=$plain_dir branch=$plain_branch out=$OUT err=$ERRTEXT"; fi

  # --- 28. a dry run previews the deferral a live run would make.
  if [[ "$(id -u)" != 0 ]]; then
    mk_repo twentyeight
    add_wt "$SHARED" review/previewgone "$ROOT/twentyeight-gone"
    rm -rf "$ROOT/twentyeight-gone" || die "rm failed"
    add_wt "$SHARED" review/sealed28 "$ROOT/twentyeight-sealed"
    chmod 000 "$ROOT/twentyeight-sealed" || die "chmod failed"
    run "$SHARED" --dry-run
    chmod 755 "$ROOT/twentyeight-sealed" || die "chmod restore failed"
    echo "28. a dry run does not promise a deletion the live run would defer"
    if (( RC == 2 )) && [[ "$(branches_deleted)" != *review/previewgone* ]] && has_branch "$SHARED" review/previewgone; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  fi

  # --- 29. a longer branch's config is not this branch's config.
  mk_repo twentynine
  # --no-track: a tracking branch has its own branch.<name>.* section, and
  # removing that would succeed whether or not the probe matched the sibling.
  git -C "$SHARED" branch --no-track review/foo origin/main || die "branch failed"
  # Section `review/foo.bar`, key `remote` — `branch.review/foo.` prefixes it,
  # but it belongs to another branch entirely.
  git -C "$SHARED" config "branch.review/foo.bar.remote" origin || die "config failed"
  run "$SHARED"
  echo "29. a sibling branch's config section is not mistaken for this branch's"
  sibling_kept=0
  config_rc=0
  git -C "$SHARED" config --get "branch.review/foo.bar.remote" >/dev/null || config_rc=$?
  case "$config_rc" in 0) sibling_kept=1 ;; 1) ;; *) die "git config --get failed (exit $config_rc)" ;; esac
  if (( RC == 0 )) && [[ "$(branches_deleted)" == *review/foo* ]] && ! has_branch "$SHARED" review/foo && (( sibling_kept )); then pass; else fail "rc=$RC sibling=$sibling_kept out=$OUT err=$ERRTEXT"; fi

  # --- 30. a branch a worktree holds is kept even when the inventory missed it.
  mk_repo thirty
  add_wt "$SHARED" review/claimed "$ROOT/thirty-claimed"
  mkdir -p "$TMP/shim30" || die "mkdir shim failed"
  # The run's own inventory read comes back empty, so the branch reaches the
  # branch pass as if no worktree held it; every later read is the real thing.
  cat > "$TMP/shim30/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"worktree list"*-z*)
    if [[ ! -e "$TMP/shim30/seen" ]]; then : > "$TMP/shim30/seen"; exit 0; fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim30/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim30:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "30. a branch checked out in a worktree the inventory missed is kept"
  if (( RC == 0 )) && [[ "$(branch_kept_reason review/claimed)" == checked-out ]] && has_branch "$SHARED" review/claimed && [[ -d "$ROOT/thirty-claimed" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 31. a worktree claiming the branch inside the deletion window.
  mk_repo thirtyone
  git -C "$SHARED" branch review/raced origin/main || die "branch failed"
  mkdir -p "$TMP/shim31" || die "mkdir shim failed"
  # `update-ref -d` has no checked-out guard: the shim claims the branch just
  # before the deletion lands, exactly the race the inventory cannot see.
  cat > "$TMP/shim31/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"update-ref -d refs/heads/review/raced"*)
    if [[ ! -e "$TMP/shim31/seen" ]]; then
      : > "$TMP/shim31/seen"
      if ! "$(command -v git)" -C "$SHARED" worktree add -q "$ROOT/thirtyone-raced" review/raced >/dev/null; then
        echo "shim31: fixture could not claim review/raced" >&2
      fi
    fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim31/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim31:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "31. a branch claimed while it was being deleted is put back"
  if (( RC == 2 )) && has_branch "$SHARED" review/raced && [[ "$OUT" == *"was restored at"* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 33. a post-deletion occupancy read that fails is not "nothing holds it".
  mk_repo thirtythree
  git -C "$SHARED" branch --no-track review/unreadable origin/main || die "branch failed"
  mkdir -p "$TMP/shim33" || die "mkdir shim failed"
  # The reads before the deletion answer; the one after it fails, so the run
  # must say the safety check did not run rather than report a clean deletion.
  cat > "$TMP/shim33/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"worktree list"*-z*)
    if [[ -e "$TMP/shim33/deleted" ]]; then echo "fatal: fixture inventory failure" >&2; exit 128; fi ;;
  *"update-ref -d refs/heads/review/unreadable"*) : > "$TMP/shim33/deleted" ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim33/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim33:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "33. a failed post-deletion occupancy read is reported, not read as unoccupied"
  if (( RC == 2 )) && [[ "$OUT" == *"could not be read"* ]] && [[ "$OUT" == *"fixture inventory failure"* ]] && [[ "$(branches_deleted)" != *review/unreadable* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 32. a parent whose own name ends in a newline still confirms absence.
  mk_repo thirtytwo
  nl_parent="$ROOT/thirtytwo-p"$'\n'
  mkdir -p "$nl_parent" || die "mkdir newline parent failed"
  git -C "$SHARED" worktree add -q -b review/nlparent "$nl_parent/wt" origin/main 2>/dev/null \
    || die "fixture could not create a worktree under a newline-bearing parent"
  rm -rf "$nl_parent/wt" || die "rm failed"
  run "$SHARED"
  echo "32. absence is confirmed through a parent whose name ends in a newline"
  if (( RC == 0 )) && [[ "$ERRTEXT" != *"cannot confirm the worktree is gone"* ]] && ! has_branch "$SHARED" review/nlparent; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 34. the config cleanup will not delete a recreated branch's section.
  # The window is narrow: after the post-deletion occupancy re-check says the
  # branch is free, and before `--remove-section` runs. The shim recreates the
  # branch during the config READ, which sits exactly in it.
  mk_repo thirtyfour
  git -C "$SHARED" branch --no-track review/recreated origin/main || die "branch failed"
  git -C "$SHARED" config "branch.review/recreated.description" "original" || die "config failed"
  mkdir -p "$TMP/shim34" || die "mkdir shim failed"
  cat > "$TMP/shim34/git" <<SHIM || die "shim write failed"
#!/usr/bin/env bash
set -euo pipefail
case "\$*" in
  *"config --get-regexp"*)
    if [[ ! -e "$TMP/shim34/seen" ]]; then
      : > "$TMP/shim34/seen"
      if ! "$(command -v git)" -C "$SHARED" worktree add -q --track -b review/recreated "$ROOT/thirtyfour-live" origin/main >/dev/null 2>&1; then
        echo "shim34: fixture could not recreate review/recreated" >&2
      fi
    fi ;;
esac
exec "$(command -v git)" "\$@"
SHIM
  chmod +x "$TMP/shim34/git" || die "chmod shim failed"
  RUN_SEQ=$((RUN_SEQ+1))
  OUT="$(env WORKTREE_ROOT="$ROOT" PATH="$TMP/shim34:$PATH" bash "$SCRIPT" "$SHARED" 2>"$TMP/err.$RUN_SEQ")"; RC=$?; ERRTEXT="$(cat "$TMP/err.$RUN_SEQ")"
  echo "34. a branch.<name> section recreated after the deletion is left untouched"
  kept_config=0
  config_rc=0
  git -C "$SHARED" config --get "branch.review/recreated.remote" >/dev/null || config_rc=$?
  case "$config_rc" in 0) kept_config=1 ;; 1) ;; *) die "git config --get failed (exit $config_rc)" ;; esac
  if (( kept_config )) && [[ "$OUT" == *"remove nothing by hand"* ]] && [[ "$OUT" == *'"branches_deleted": ['*'review/recreated'* ]]; then
    pass; else fail "the recreated branch's config must survive and be reported: rc=$RC out=$OUT err=$ERRTEXT"; fi

  # --- 14. usage / not a repo.
  run
  echo "14a. usage is exit 1 with no JSON"
  if (( RC == 1 )) && [[ -z "$OUT" ]] && [[ "$ERRTEXT" == *usage* ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  mkdir -p "$TMP/notrepo" || die "mkdir failed"
  run "$TMP/notrepo"
  echo "14b. a non-repository is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi
  run "$SHARED" --bogus
  echo "14c. an unknown flag is exit 1"
  if (( RC == 1 )) && [[ -z "$OUT" ]]; then pass; else fail "rc=$RC out=$OUT err=$ERRTEXT"; fi

  echo
  echo "passed=$PASS failed=$FAIL"
  (( FAIL == 0 ))
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
