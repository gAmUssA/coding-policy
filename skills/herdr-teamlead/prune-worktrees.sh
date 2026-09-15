#!/usr/bin/env bash
# Remove the worker worktrees and local branches a round left behind.
#
# Every Herdr round provisions worktrees under the worktree root
# (`provision-worktree.sh`) and the lead removes the task's own worktree after
# its merge (`rules/agent-worktree-isolation.md` Cleanup). Review, test and
# verify worktrees from earlier rounds, and the local branches a removed
# worktree leaves behind, accumulate until someone notices `git worktree list`
# scrolling. Which of them are safe to remove is one right answer per input,
# so the decision lives here (`rules/script-delegation.md`).
#
# Decision predicate — a worktree is REMOVED (and its branch deleted) iff:
#   * it is not the shared checkout itself,
#   * it lies under the worktree root,
#   * it is on a branch (not detached) other than origin's default branch,
#   * it is not locked,
#   * `git status --porcelain --untracked-files=all` is empty — untracked
#     files count as dirty whatever `status.showUntrackedFiles` says;
#     ignored files do not, they are reproducible by the ignore's own claim
#     and `git worktree remove` treats them the same way,
#   * its branch is an ancestor of origin's default branch (fully merged),
#     judged against refs fetched by THIS run and origin's default branch as
#     re-queried by THIS run — a fetch or default-branch lookup that fails is
#     a precondition failure, never a judgment from stale refs.
# A local branch with no worktree is DELETED iff it is not the default branch
# and is an ancestor of origin's default branch. That ancestry check is the
# safety, and it judges a captured commit rather than a name that can move.
# `update-ref` carries no checked-out-worktree guard of its own, and the
# inventory is a snapshot, so occupancy is re-read from git either side of
# every deletion: a branch a worktree holds is KEPT with reason checked-out,
# and one claimed inside the deletion's own window is restored at the commit
# it was deleted from.
# Deletion is then `git update-ref -d refs/heads/<branch> <that commit>`,
# git's compare-and-delete: it removes the branch only while it still points
# at the commit just proved merged, so a commit landing mid-run keeps the
# branch instead of being force-deleted. `branch -d` would re-derive the
# safety against the local default, which may lag origin's, and `branch -D`
# would skip it entirely; neither is atomic with the check. Removal is `git worktree remove`,
# never `rm -rf`. Everything else is KEPT and reported with its reason. Stale
# worktree metadata is pruned (`git worktree prune --expire now`) after every
# worktree decision and before the branch pass, so a confirmed-gone entry's
# merged branch goes in the same run; the prune is skipped when any worktree
# could not be entered, since git would read an unreadable directory as gone
# and drop its entry. Nothing here
# pushes to origin; a dry run still fetches (without --prune), reads origin's
# default branch with `ls-remote --symref` instead of rewriting origin/HEAD,
# and skips the metadata prune, so its decisions are current and .git is
# otherwise untouched.
#
# Contract:
#   argv  : <shared-checkout> [--dry-run]
#           --dry-run reports the same decisions and removes nothing. It still
#           fetches, so its answer is current: remote-tracking refs, FETCH_HEAD
#           and the object database move as any fetch moves them. No worktree,
#           branch, config or metadata entry changes.
#   stdout: one JSON object —
#           {"shared":"<abs>","default_branch":"<name>","dry_run":bool,
#            "worktrees_removed":[{"path","branch"}],
#            "worktrees_kept":[{"path","branch","reason"}],
#            "branches_deleted":["<name>"],
#            "branches_kept":[{"branch","reason"}],
#            "failed":[{"target","error"}]}
#           reason is one of: checked-out (a worktree claimed the branch after
#           the inventory was taken), default-branch, detached, dirty, locked,
#           outside-root, prunable (its directory is gone; a live run's
#           metadata prune removes it), unmerged.
#   stderr: diagnostics only.
#   exit  : 0 every decision applied (or previewed),
#           1 precondition unmet (usage, git or python3 absent, not a repo,
#             no origin, fetch failed, default branch unresolvable, worktree
#             or branch inventory unreadable) — no JSON, nothing decided,
#           2 at least one check, removal or deletion failed; the rest still
#             ran and `failed` names each one.
#   env   : WORKTREE_ROOT overrides the worktree root (default
#           $HOME/.worktrees); the tests point it at a temp dir.
set -euo pipefail

ERRFILE=""
ROWS=""

warn() { printf 'prune-worktrees: %s\n' "$1" >&2; }

cleanup() {
  local f
  for f in "$ERRFILE" "$ROWS"; do
    if [[ -n "$f" ]] && ! rm -f "$f"; then
      warn "could not remove temp file ${f} — remove it by hand"
    fi
  done
  return 0
}

# Append one decision row: <kind> <target> <branch> <reason>, NUL-delimited
# so a path or diagnostic holding a tab or newline cannot shift the fields.
row() {
  printf '%s\0%s\0%s\0%s\0' "$1" "$2" "$3" "$4" >> "$ROWS"
  # A failure is a diagnostic on stderr as well as a JSON row
  # (rules/script-delegation.md Script Requirements).
  if [[ "$1" == failed ]]; then
    warn "${2}: ${4} — inspect it by hand; nothing else was skipped on its account"
  fi
}

# 0 when <ref> exists, 1 when it is absent; any other show-ref exit is a tool
# failure, warned about and returned as 2 so no fallback runs on top of it.
ref_exists() { # <shared> <ref>
  local rc=0
  git -C "$1" show-ref --verify --quiet "$2" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0|1) return "$rc" ;;
    *) warn "\`git show-ref --verify ${2}\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"; return 2 ;;
  esac
}

# Echo origin's default branch name, or return 1 when none can be confirmed.
# A live run has just rewritten origin/HEAD from the remote; a dry run reads
# the remote's HEAD with `ls-remote --symref` instead, rewriting nothing.
default_branch_of() { # <shared> <dry-run 0|1>
  local db="" rc=0
  if (( $2 )); then
    local sym
    sym="$(git -C "$1" ls-remote --symref origin HEAD 2>"$ERRFILE")" || rc=$?
    if (( rc != 0 )); then
      warn "\`git ls-remote --symref origin HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"
      return 1
    fi
    db="$(printf '%s\n' "$sym" | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -n 1)"
    if [[ -n "$db" ]]; then
      # The remote named its default; a missing local origin/<name> is a
      # refspec or fetch problem, never a reason to guess main or master.
      ref_exists "$1" "refs/remotes/origin/${db}" || rc=$?
      case "$rc" in
        0) printf '%s' "$db"; return 0 ;;
        1) warn "origin reports default branch '${db}' but refs/remotes/origin/${db} is absent after the fetch — check the fetch refspec"; return 1 ;;
        *) return 1 ;;
      esac
    fi
  else
    # The full target, not --short: a local branch named origin/<x> makes the
    # short form ambiguous and git renders it as remotes/origin/<x>.
    db="$(git -C "$1" symbolic-ref --quiet refs/remotes/origin/HEAD 2>"$ERRFILE")" || rc=$?
    case "$rc" in
      0) if [[ "$db" != refs/remotes/origin/* ]]; then
           warn "origin/HEAD points at '${db}', not a refs/remotes/origin/ ref — run \`git remote set-head origin --auto\`"; return 1
         fi
         db="${db#refs/remotes/origin/}"
         # origin/HEAD was just rewritten from the remote; a dangling one is a
         # refspec or fetch problem, never a reason to guess main or master.
         rc=0; ref_exists "$1" "refs/remotes/origin/${db}" || rc=$?
         case "$rc" in
           0) printf '%s' "$db"; return 0 ;;
           1) warn "origin/HEAD names '${db}' but refs/remotes/origin/${db} is absent after the fetch — check the fetch refspec"; return 1 ;;
           *) return 1 ;;
         esac ;;
      1) ;;  # origin/HEAD is simply absent: fall back to the conventional names
      *) warn "\`git symbolic-ref refs/remotes/origin/HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE")"; return 1 ;;
    esac
  fi
  local cand
  for cand in main master; do
    rc=0; ref_exists "$1" "refs/remotes/origin/$cand" || rc=$?
    case "$rc" in 0) printf '%s' "$cand"; return 0 ;; 1) ;; *) return 1 ;; esac
  done
  return 1
}

# Echo merged|unmerged for <commit> against refs/remotes/origin/<default>,
# or return 2 on a tool failure. The caller passes the commit it captured, so
# a commit landing after the capture cannot become the judged tip.
# `merge-base --is-ancestor` exits 1 for "not an ancestor" and
# anything else for an invalid ref or repository error; collapsing both into
# "unmerged" would hide the failure behind a kept row
# (rules/error-handling.md Shell Error Handling).
ancestry() { # <shared> <commit> <default>
  local rc=0
  # The default side is fully qualified: a tag or a local branch named like it
  # would otherwise shadow it. The candidate side is already a commit id.
  git -C "$1" merge-base --is-ancestor "$2" "refs/remotes/origin/$3" 2>"$ERRFILE" || rc=$?
  case "$rc" in
    0) printf 'merged' ;;
    1) printf 'unmerged' ;;
    *) return 2 ;;
  esac
  return 0
}

# Echo the commit refs/heads/<branch> points at, or return 1 on any failure.
branch_tip() { # <shared> <branch>
  local out rc=0
  out="$(git -C "$1" rev-parse --verify --quiet "refs/heads/$2^{commit}" 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$out" ]]; then
    return 1
  fi
  printf '%s' "$out"
}

# Echo 1 when some worktree currently has <branch> checked out and 0 when none
# does; return 1 when the inventory cannot be read. `update-ref -d` carries
# none of git's own checked-out-worktree guard, and the run's inventory is a
# snapshot: a `worktree add` claiming the branch after it was taken is
# invisible to `seen_branches` (#410). So occupancy is re-read at the deletion.
branch_checked_out() { # <shared> <branch>
  local listing rc=0 field found=0
  # The caller builds its failure row from ERRFILE, so a bare `return 1` here
  # would report the occupancy read as failing for whatever the previous
  # command left there (#426).
  listing="$(mktemp 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$listing" ]]; then
    printf 'mktemp failed (exit %s): %s\n' "${rc:-0}" "$(tr '\n' ' ' < "$ERRFILE")" > "$ERRFILE"
    return 1
  fi
  if ! git -C "$1" worktree list --porcelain -z >"$listing" 2>"$ERRFILE"; then
    if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
    return 1
  fi
  while IFS= read -r -d '' field || [[ -n "$field" ]]; do
    if [[ "$field" == "branch refs/heads/$2" ]]; then found=1; break; fi
  done < "$listing"
  if ! rm -f "$listing"; then warn "could not remove temp file ${listing} — remove it by hand"; fi
  printf '%s' "$found"
}

# Delete <branch> only if it still points at <tip>, atomically: `update-ref -d`
# with an old value is git's compare-and-delete, so no commit can land between
# the check and the deletion the way a read-then-`branch -D` allows (#405).
# The ancestry proof is the safety `branch -d` would otherwise re-derive
# against the local default, which may lag origin's. Occupancy is re-read
# either side of it: before, so a worktree created since the inventory is
# seen; after, so one created inside that window is put back rather than left
# checked out on a branch that no longer exists (#410).
delete_branch() { # <shared> <branch> <tip>  -> 0 deleted, 1 moved, 2 git refused, 3 deleted but config left, 4 occupancy unreadable, 5 checked out, 6 claimed mid-run and restored, 7 claimed mid-run and not restored, 8 deleted and post-deletion occupancy unreadable, 9 deleted and its config section left to a branch recreated since
  local rc=0 now refusal occupied
  if ! occupied="$(branch_checked_out "$1" "$2")"; then
    return 4
  fi
  if [[ "$occupied" == 1 ]]; then
    return 5
  fi
  git -C "$1" update-ref -d "refs/heads/$2" "$3" 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    # Hold git's own words: the re-read below truncates ERRFILE, and a
    # refusal the caller reports without them is undiagnosable.
    refusal="$(tr '\n' ' ' < "$ERRFILE")"
    if now="$(branch_tip "$1" "$2")" && [[ "$now" != "$3" ]]; then
      return 1
    fi
    printf '%s' "$refusal" > "$ERRFILE"
    return 2
  fi
  # The window between the check above and the deletion is small, not empty.
  # A worktree that claimed the branch inside it is now checked out on a ref
  # that is gone, so the branch goes back at the commit it was deleted from —
  # `update-ref` with an empty old value creates it only while it is still
  # absent, so a racing `worktree add -b` that made its own is left alone.
  # A failed read here is not "no worktree holds it": returning to the config
  # probe below would overwrite this diagnostic and report a clean deletion
  # whose safety check never ran (rules/error-handling.md Shell Error
  # Handling — an expected non-result is not a tool failure).
  if ! occupied="$(branch_checked_out "$1" "$2")"; then
    return 8
  fi
  if [[ "$occupied" == 1 ]]; then
    if git -C "$1" update-ref "refs/heads/$2" "$3" "" 2>"$ERRFILE"; then
      return 6
    fi
    return 7
  fi
  # `update-ref` leaves the branch config `branch -D` would have removed.
  # Whether there is any is read first: `--remove-section` exits 128 for a
  # missing section and for a malformed config alike, so the exit code alone
  # cannot tell an expected non-result from a failure.
  local keys=""
  rc=0
  keys="$(git -C "$1" config --get-regexp '^branch\.' 2>"$ERRFILE")" || rc=$?
  case "$rc" in
    0) ;;
    1) return 0 ;;  # no branch.* config at all
    *) return 3 ;;
  esac
  # Section equality, not a substring: `branch.foo.` also prefixes
  # `branch.foo.bar.remote`, which belongs to the branch `foo.bar`. Reading
  # that as `foo`'s config makes `--remove-section branch.foo` fail on a
  # section that was never there, and the run reports a cleanup failure it
  # invented (#410). A config key is `branch.<name>.<key>` and `<name>` may
  # hold dots, so the name is what sits between the first dot and the last.
  local keyline key section found=0
  while IFS= read -r keyline; do
    key="${keyline%% *}"
    [[ "$key" == branch.*.* ]] || continue
    section="${key#branch.}"
    section="${section%.*}"
    if [[ "$section" == "$2" ]]; then found=1; break; fi
  done <<<"$keys"
  if (( ! found )); then
    return 0
  fi
  # The section may have been recreated since the deletion: `worktree add
  # --track -b` writes a fresh `branch.<name>.*` for the NEW branch, and
  # removing it then deletes configuration belonging to a live checkout (#426).
  # Occupancy is re-read immediately before the removal, the same guard the
  # deletion itself takes.
  local now_held
  if ! now_held="$(branch_checked_out "$1" "$2")"; then
    # The deletion already happened; 4 would tell the caller it did not.
    return 8
  fi
  if [[ "$now_held" == 1 ]]; then
    return 9
  fi
  rc=0
  git -C "$1" config --remove-section "branch.$2" >/dev/null 2>"$ERRFILE" || rc=$?
  if (( rc != 0 )); then
    return 3
  fi
  return 0
}

# Decide one worktree; emits a row and performs the removal unless dry-run.
#: Set by `decide_worktree` when, and only when, it decided `prunable`. Git
#: keeps a locked entry's metadata through `worktree prune`, so a locked entry
#: whose directory is gone stays checked out and its branch must not be
#: released.
DECIDED_PRUNABLE=0

decide_worktree() { # <shared> <abs_root> <default> <dry-run 0|1> <path> <branch|""> <detached 0|1> <locked 0|1>
  DECIDED_PRUNABLE=0
  local shared="$1" abs_root="$2" db="$3" dry="$4" path="$5" branch="$6" detached="$7" locked="$8"
  if [[ "$path" != "$abs_root"/* ]]; then
    row kept "$path" "$branch" outside-root; return 0
  fi
  if (( detached )); then
    row kept "$path" "" detached; return 0
  fi
  if [[ "$branch" == "$db" ]]; then
    row kept "$path" "$branch" default-branch; return 0
  fi
  if (( locked )); then
    row kept "$path" "$branch" locked; return 0
  fi
  if [[ ! -d "$path" ]]; then
    # The caller releases this branch to the branch pass once the metadata
    # prune runs; an entry kept for any earlier reason never reaches here.
    DECIDED_PRUNABLE=1
    row kept "$path" "$branch" prunable; return 0
  fi
  local status rc=0
  # Explicit untracked mode: status.showUntrackedFiles=no would hide the
  # very files the dirty check exists to protect.
  status="$(git -C "$path" status --porcelain --untracked-files=all 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )); then
    row failed "$path" "$branch" "git status failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ -n "$status" ]]; then
    row kept "$path" "$branch" dirty; return 0
  fi
  # The tip is captured before it is judged, and the deletion below is
  # conditional on that same commit, so one commit answers both (#405).
  local tip merged
  if ! tip="$(branch_tip "$shared" "$branch")"; then
    row failed "$path" "$branch" "cannot read the tip of ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if ! merged="$(ancestry "$shared" "$tip" "$db")"; then
    row failed "$path" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ "$merged" == unmerged ]]; then
    row kept "$path" "$branch" unmerged; return 0
  fi
  if (( dry )); then
    row removed "$path" "$branch" ""; return 0
  fi
  if ! git -C "$shared" worktree remove "$path" 2>"$ERRFILE"; then
    row failed "$path" "$branch" "git worktree remove failed: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  # The removal happened: report it whatever the deletion does, so the JSON
  # matches the disk a retry would find (#405).
  row removed "$path" "$branch" ""
  local rc=0
  delete_branch "$shared" "$branch" "$tip" || rc=$?
  case "$rc" in
    0) ;;
    1) row failed "$branch" "$branch" "branch ${branch} moved after its ancestry check and was left alone; its worktree is already removed, so re-run to judge the new tip" ;;
    3) row failed "$branch" "$branch" "${branch} is deleted but its branch.${branch} config could not be cleaned up: $(tr '\n' ' ' < "$ERRFILE") — check and remove it by hand" ;;
    4) row failed "$branch" "$branch" "could not re-read which worktrees hold ${branch} after its own was removed, so it was left alone: $(tr '\n' ' ' < "$ERRFILE")" ;;
    5) row failed "$branch" "$branch" "another worktree claimed ${branch} after its own was removed, so it was left alone; re-run once that worktree is gone" ;;
    6) row failed "$branch" "$branch" "another worktree claimed ${branch} while it was being deleted; the branch was restored at ${tip} and that worktree is intact — re-run once it is gone" ;;
    7) row failed "$branch" "$branch" "another worktree claimed ${branch} while it was being deleted and it could not be restored: $(tr '\n' ' ' < "$ERRFILE") — inspect that worktree by hand" ;;
    8) row failed "$branch" "$branch" "${branch} is deleted and whether a worktree claimed it meanwhile could not be read: $(tr '\n' ' ' < "$ERRFILE") — check \`git worktree list\` and restore ${branch} at ${tip} if one holds it" ;;
    9) row failed "$branch" "$branch" "${branch} is deleted, and a worktree recreated a branch of that name before its config could be cleaned up — branch.${branch} belongs to that live branch and was left untouched; remove nothing by hand" ;;
    *) row failed "$branch" "$branch" "deleting ${branch} failed after the worktree was removed: $(tr '\n' ' ' < "$ERRFILE")" ;;
  esac
  return 0
}

decide_branch() { # <shared> <default> <dry-run 0|1> <branch>
  local shared="$1" db="$2" dry="$3" branch="$4"
  if [[ "$branch" == "$db" ]]; then
    return 0
  fi
  local tip merged rc=0
  if ! tip="$(branch_tip "$shared" "$branch")"; then
    row failed "$branch" "$branch" "cannot read the tip of ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if ! merged="$(ancestry "$shared" "$tip" "$db")"; then
    row failed "$branch" "$branch" "git merge-base failed for ${branch}: $(tr '\n' ' ' < "$ERRFILE")"; return 0
  fi
  if [[ "$merged" == unmerged ]]; then
    row branch-kept "$branch" "$branch" unmerged; return 0
  fi
  if (( dry )); then
    row branch-deleted "$branch" "$branch" ""; return 0
  fi
  delete_branch "$shared" "$branch" "$tip" || rc=$?
  case "$rc" in
    0) row branch-deleted "$branch" "$branch" "" ;;
    1) row failed "$branch" "$branch" "branch ${branch} moved after its ancestry check and was left alone; re-run to judge the new tip" ;;
    3) row branch-deleted "$branch" "$branch" ""
       row failed "$branch" "$branch" "${branch} is deleted but its branch.${branch} config could not be cleaned up: $(tr '\n' ' ' < "$ERRFILE") — check and remove it by hand" ;;
    4) row failed "$branch" "$branch" "could not re-read which worktrees hold ${branch}, so it was left alone: $(tr '\n' ' ' < "$ERRFILE")" ;;
    5) row branch-kept "$branch" "$branch" checked-out ;;
    6) row failed "$branch" "$branch" "a worktree claimed ${branch} while it was being deleted; the branch was restored at ${tip} and that worktree is intact — re-run once it is gone" ;;
    7) row failed "$branch" "$branch" "a worktree claimed ${branch} while it was being deleted and it could not be restored: $(tr '\n' ' ' < "$ERRFILE") — inspect that worktree by hand" ;;
    8) row failed "$branch" "$branch" "${branch} is deleted and whether a worktree claimed it meanwhile could not be read: $(tr '\n' ' ' < "$ERRFILE") — check \`git worktree list\` and restore ${branch} at ${tip} if one holds it" ;;
    9) row branch-deleted "$branch" "$branch" ""
       row failed "$branch" "$branch" "${branch} is deleted, and a worktree recreated a branch of that name before its config could be cleaned up — branch.${branch} belongs to that live branch and was left untouched; remove nothing by hand" ;;
    *) row failed "$branch" "$branch" "deleting ${branch} failed: $(tr '\n' ' ' < "$ERRFILE")" ;;
  esac
  return 0
}

main() {
  local shared="" dry=0 arg
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=1 ;;
      -*) warn "unknown flag '${arg}'"; warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"; return 1 ;;
      *) if [[ -n "$shared" ]]; then warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"; return 1; fi; shared="$arg" ;;
    esac
  done
  if [[ -z "$shared" ]]; then
    warn "usage: prune-worktrees.sh <shared-checkout> [--dry-run]"
    return 1
  fi
  local tool
  for tool in git python3; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      warn "${tool} not found on PATH"
      return 1
    fi
  done
  ERRFILE="$(mktemp)"
  ROWS="$(mktemp)"
  trap cleanup EXIT
  if [[ ! -d "$shared" ]] || ! git -C "$shared" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "'${shared}' is not a git work tree — pass the shared checkout's path"
    return 1
  fi
  local abs_shared
  abs_shared="$(git -C "$shared" rev-parse --show-toplevel)"
  if ! git -C "$shared" remote get-url origin >/dev/null 2>&1; then
    warn "${shared} has no origin remote — merged-ness is judged against origin's default branch"
    return 1
  fi
  local root="${WORKTREE_ROOT:-${HOME}/.worktrees}" abs_root
  if [[ ! -d "$root" ]]; then
    warn "worktree root ${root} does not exist — nothing to prune"
    abs_root="$root"
  else
    abs_root="$(cd "$root" && pwd -P)"
  fi
  # Merged-ness is only as good as the refs it is judged against: a stale
  # origin/<default> after a force-push, or a cached origin/HEAD after the
  # remote's default branch moved, would delete work origin no longer holds.
  local -a fetch_args=(fetch --quiet origin)
  if (( ! dry )); then fetch_args=(fetch --quiet --prune origin); fi
  if ! git -C "$shared" "${fetch_args[@]}" 2>"$ERRFILE"; then
    warn "\`git -C ${shared} ${fetch_args[*]}\` failed: $(tr '\n' ' ' < "$ERRFILE") — check connectivity; refusing to judge merged-ness from stale refs"
    return 1
  fi
  if (( ! dry )) && ! git -C "$shared" remote set-head origin --auto >/dev/null 2>"$ERRFILE"; then
    warn "\`git -C ${shared} remote set-head origin --auto\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot confirm origin's current default branch"
    return 1
  fi
  local db
  if ! db="$(default_branch_of "$shared" "$dry")"; then
    warn "cannot resolve origin's default branch — run \`git -C ${shared} remote set-head origin --auto\`"
    return 1
  fi

  # Take both inventories up front: a failure inside a process substitution
  # would not reach the loop, and an empty inventory would read as "nothing
  # to prune" with exit 0 (rules/file-hygiene.md I/O Conventions). Nothing has
  # been decided yet, so an unreadable inventory is a precondition failure.
  local inventory branches
  inventory="$(mktemp)"; branches="$(mktemp)"
  # `-z` (git >= 2.36) terminates each attribute with NUL, so a path holding a
  # newline stays one field. Without it there is no unambiguous read and no
  # cross-check that settles one: a scan refusing unrecognized lines still
  # accepts a path whose tail reads as an attribute (`...<newline>HEAD <sha>`,
  # `...<newline>branch refs/heads/other`), and the parser then takes that
  # tail for the record's metadata. So an inventory without `-z` decides
  # nothing (#410).
  if ! git -C "$shared" worktree list --porcelain -z >"$inventory" 2>"$ERRFILE"; then
    if grep -qiE 'unknown option|usage: git worktree' "$ERRFILE"; then
      warn "\`git worktree list --porcelain -z\` is unavailable (git < 2.36): a worktree path cannot be listed unambiguously — upgrade git to 2.36 or newer; nothing was decided"
    else
      warn "\`git worktree list --porcelain -z\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory worktrees"
    fi
    rm -f "$inventory" "$branches"
    return 1
  fi
  # Full refnames: `%(refname:short)` renders a local branch named like a
  # remote-tracking ref (origin/main) as heads/origin/main.
  if ! git -C "$shared" for-each-ref --format='%(refname)' refs/heads/ >"$branches" 2>"$ERRFILE"; then
    warn "\`git for-each-ref refs/heads/\` failed: $(tr '\n' ' ' < "$ERRFILE") — cannot inventory branches"
    rm -f "$inventory" "$branches"
    return 1
  fi

  # Walk `worktree list --porcelain`: blank-line-separated blocks.
  local path="" branch="" detached=0 locked=0 line unenterable=0
  local -a seen_branches=() prunable_branches=()
  flush() {
    if [[ -n "$path" ]]; then
      local real="" rc=0 parent
      # The sentinel guards the same truncation `pwd -P` needs it for below:
      # when the PARENT's own name ends in a newline, `dirname`'s output ends
      # in two and command substitution strips both (#410).
      parent="$(dirname "$path" && printf 'x')"
      parent="${parent%x}"
      parent="${parent%$'\n'}"
      if [[ ! -e "$path" ]]; then
        # `-e` is false for a missing path and for one whose ancestor denies
        # traversal. Absence is confirmed only through a traversable parent;
        # anything else is a failure that also inhibits the metadata prune.
        if [[ -d "$parent" && -x "$parent" ]]; then
          # Confirmed gone: reported prunable. Its branch goes to the
          # no-worktree pass only once the metadata prune actually releases
          # it, so it is recorded here and released below.
          decide_worktree "$shared" "$abs_root" "$db" "$dry" "$path" "$branch" "$detached" "$locked"
          if [[ -n "$branch" ]]; then
            if (( DECIDED_PRUNABLE )); then
              prunable_branches+=("$branch")
            else
              # Kept for a reason that outranks prunable (locked, detached,
              # outside the root, the default branch): still checked out.
              seen_branches+=("$branch")
            fi
          fi
          path=""; branch=""; detached=0; locked=0
          return 0
        else
          row failed "$path" "$branch" "cannot confirm the worktree is gone: its parent ${parent} is missing or not traversable"
          # Still checked out as far as git knows: the branch pass must not
          # try `branch -D` on it (#405).
          if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
          unenterable=1
          path=""; branch=""; detached=0; locked=0
          return 0
        fi
      else
        # A sentinel past `pwd`'s own newline: command substitution strips
        # trailing newlines, and a worktree path may end in one now that `-z`
        # can carry it. Without this the run would decide against a truncated
        # path.
        real="$(cd "$path" 2>"$ERRFILE" && pwd -P && printf 'x')" || rc=$?
        real="${real%x}"
        real="${real%$'\n'}"
        if (( rc != 0 )); then
          row failed "$path" "$branch" "cannot enter the worktree: $(tr '\n' ' ' < "$ERRFILE")"
          if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
          unenterable=1
          path=""; branch=""; detached=0; locked=0
          return 0
        fi
      fi
      if [[ -n "$branch" ]]; then seen_branches+=("$branch"); fi
      if [[ "$real" != "$abs_shared" ]]; then
        decide_worktree "$shared" "$abs_root" "$db" "$dry" "$real" "$branch" "$detached" "$locked"
      fi
    fi
    path=""; branch=""; detached=0; locked=0
  }
  while IFS= read -r -d '' line || [[ -n "$line" ]]; do
    case "$line" in
      "worktree "*) flush; path="${line#worktree }" ;;
      "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
      detached) detached=1 ;;
      "locked"*) locked=1 ;;
      "") flush ;;
    esac
  done < "$inventory"
  flush

  # Metadata prune between the passes: after every worktree decision, so git reads an unreadable worktree directory as gone
  # a confirmed-gone entry is released before its branch is judged below;
  # skipped when a worktree could not be entered, since git reads an unreadable
  # directory as gone and would drop its entry.
  # A prunable entry's branch is still checked out until its metadata goes,
  # so it is released to the branch pass only when the prune ran clean. A dry
  # run previews the live outcome, where the prune does run (#405).
  local released=1
  # An unenterable worktree stops the prune in a live run, so a dry run defers
  # the same branches: a preview that promises a deletion the live run would
  # not make is worse than no preview.
  if (( unenterable )); then released=0; fi
  if (( ! dry )); then
    if (( unenterable )); then
      warn "skipping \`git worktree prune\`: a worktree could not be entered; restore access and re-run"
    elif ! git -C "$shared" worktree prune --expire now 2>"$ERRFILE"; then
      # Recorded, not merely warned: the run continues, the exit stays non-zero.
      row failed "git worktree prune" "" "failed: $(tr '\n' ' ' < "$ERRFILE") — stale metadata may remain"
      released=0
    fi
  fi
  if (( ! released )); then
    seen_branches+=("${prunable_branches[@]+"${prunable_branches[@]}"}")
  fi

  # Local branches with no worktree.
  local name skip
  while IFS= read -r name; do
    name="${name#refs/heads/}"
    skip=0
    local s
    for s in "${seen_branches[@]+"${seen_branches[@]}"}"; do
      if [[ "$s" == "$name" ]]; then skip=1; break; fi
    done
    if (( skip )); then continue; fi
    decide_branch "$shared" "$db" "$dry" "$name"
  done < "$branches"
  if ! rm -f "$inventory" "$branches"; then
    warn "could not remove temp inventories ${inventory} ${branches} — remove them by hand"
  fi

  local rc=0
  python3 - "$abs_shared" "$db" "$dry" "$ROWS" <<'PY' || rc=$?
import json, sys
shared, db, dry, rows_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
result = {"shared": shared, "default_branch": db, "dry_run": dry, "worktrees_removed": [], "worktrees_kept": [],
          "branches_deleted": [], "branches_kept": [], "failed": []}
with open(rows_path, "rb") as handle:
    fields = handle.read().decode("utf-8", "surrogateescape").split("\0")
if fields and fields[-1] == "":
    fields.pop()
if len(fields) % 4:
    sys.stderr.write("prune-worktrees: decision rows are malformed; report this as a bug\n")
    sys.exit(2)
for index in range(0, len(fields), 4):
    kind, target, branch, reason = fields[index:index + 4]
    if True:
        if kind == "removed":
            result["worktrees_removed"].append({"path": target, "branch": branch})
        elif kind == "kept":
            result["worktrees_kept"].append({"path": target, "branch": branch or None, "reason": reason})
        elif kind == "branch-deleted":
            result["branches_deleted"].append(branch)
        elif kind == "branch-kept":
            result["branches_kept"].append({"branch": branch, "reason": reason})
        else:
            result["failed"].append({"target": target, "error": reason})
print(json.dumps(result, sort_keys=True))
sys.exit(2 if result["failed"] else 0)
PY
  return "$rc"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
