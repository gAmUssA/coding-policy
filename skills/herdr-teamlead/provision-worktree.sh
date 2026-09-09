#!/usr/bin/env bash
# Create one worker's worktree from the shared checkout.
#
# The lead provisions; a worker never runs git against the shared checkout
# (`rules/agent-team-operation.md` Writers and Checkouts). Fetching, branch and
# path validation, and the create-or-attach decision are one right answer per
# input, so they live here rather than in the lead's hands
# (`rules/script-delegation.md`).
#
# Contract:
#   argv  : <shared-checkout> <branch> <worktree-path> [base-ref]
#           base-ref defaults to origin's default branch. It is used only when
#           the branch has to be created.
#   stdout: one JSON object —
#           {"path":"<abs>","branch":"<name>","base_ref":"<ref>",
#            "state":"created|attached|already-provisioned"}
#           `created` cut a new branch, `attached` checked out one that already
#           existed, `already-provisioned` found the path already on that
#           branch and did nothing (idempotent re-run).
#   stderr: diagnostics only.
#   exit  : 0 the worktree exists at <worktree-path> on <branch>,
#           1 precondition unmet (usage, git absent, not a repo, no origin,
#             invalid branch name, path outside the worktree root),
#           2 git refused the operation, or the path exists as something else.
#   env   : WORKTREE_ROOT overrides the required parent dir (default
#           $HOME/.worktrees); the tests point it at a temp dir.
set -euo pipefail

# Branch names follow `rules/ci-safety.md` Branch Naming: `<type>/<description>`
# lowercase with hyphens, or the accepted `<type>-<issue-number>` alternative.
BRANCH_RE='^[a-z]+(/[a-z0-9]+(-[a-z0-9]+)*|-[0-9]+)$'

ERRFILE=""

warn() { printf 'provision-worktree: %s\n' "$1" >&2; }

# Echo the ABSOLUTE common git dir for the work tree at <dir>, or return 1.
#
# The common dir is what identifies the repository: every worktree of one
# checkout resolves to the same one, and two unrelated repos never do.
# `--path-format=absolute` needs git >= 2.31, so an older git falls back to the
# relative answer resolved against the work tree itself.
common_dir_of() { # <dir>
  local dir="$1" out rc=0
  out="$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || rc=$?
  if (( rc == 0 )) && [[ -n "$out" ]]; then
    printf '%s' "$out"
    return 0
  fi
  rc=0
  out="$(git -C "$dir" rev-parse --git-common-dir 2>"$ERRFILE")" || rc=$?
  if (( rc != 0 )) || [[ -z "$out" ]]; then
    return 1
  fi
  if [[ "$out" != /* ]]; then
    out="$(cd "$dir" && cd "$out" && pwd)" || return 1
  fi
  printf '%s' "$out"
  return 0
}

cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    warn "could not remove temp file ${ERRFILE} — remove it by hand"
  fi
  return 0
}

main() {
  if (( $# < 3 || $# > 4 )); then
    warn "usage: provision-worktree.sh <shared-checkout> <branch> <worktree-path> [base-ref]"
    return 1
  fi
  local shared="$1" branch="$2" path="$3" base="${4:-}"
  local root="${WORKTREE_ROOT:-${HOME}/.worktrees}"

  if ! command -v git >/dev/null 2>&1; then
    warn "git not found on PATH"
    return 1
  fi
  ERRFILE="$(mktemp)"
  trap cleanup EXIT
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found on PATH — install it (\`brew install jq\`) to emit the result"
    return 1
  fi
  if [[ ! -d "$shared" ]] || ! git -C "$shared" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "'${shared}' is not a git work tree — pass the shared checkout's path"
    return 1
  fi
  if ! [[ "$branch" =~ $BRANCH_RE ]]; then
    warn "branch '${branch}' does not follow <type>/<description> or <type>-<number>, lowercase with hyphens (rules/ci-safety.md Branch Naming)"
    return 1
  fi

  # A worktree lives under the worktree root and nowhere else: a path inside a
  # repo, or beside the operator's projects, is how orphans and accidental
  # commits happen (`rules/agent-worktree-isolation.md`).
  local parent abs_root
  parent="$(dirname "$path")"
  if [[ ! -d "$parent" ]] && ! mkdir -p "$parent"; then
    warn "cannot create the parent dir ${parent} — check permissions"
    return 1
  fi
  abs_root="$(cd "$root" 2>/dev/null && pwd)" || {
    if ! mkdir -p "$root"; then
      warn "cannot create the worktree root ${root} — check permissions"
      return 1
    fi
    abs_root="$(cd "$root" && pwd)"
  }
  local abs_parent
  abs_parent="$(cd "$parent" && pwd)"
  if [[ "$abs_parent" != "$abs_root" && "$abs_parent" != "$abs_root"/* ]]; then
    warn "worktree path '${path}' is outside ${abs_root} — put worker worktrees under the worktree root"
    return 1
  fi
  local abs_path base_name
  base_name="$(basename "$path")"
  abs_path="${abs_parent}/${base_name}"

  if ! git -C "$shared" remote get-url origin >/dev/null 2>&1; then
    warn "${shared} has no origin remote — provisioning needs one to fetch from"
    return 1
  fi
  if ! git -C "$shared" fetch --quiet origin 2>"$ERRFILE"; then
    warn "\`git -C ${shared} fetch origin\` failed: $(tr '\n' ' ' < "$ERRFILE") — check connectivity; provisioning from possibly stale refs"
  fi

  # Resolve the default branch for the base ref, when the caller gave none.
  if [[ -z "$base" ]]; then
    local db=""
    if db="$(git -C "$shared" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null)"; then
      db="${db#origin/}"
    else
      db=""
      local cand
      for cand in main master; do
        if git -C "$shared" show-ref --verify --quiet "refs/remotes/origin/$cand"; then db="$cand"; break; fi
      done
    fi
    if [[ -z "$db" ]]; then
      warn "cannot resolve origin's default branch — pass a base-ref explicitly, or run \`git -C ${shared} remote set-head origin --auto\`"
      return 1
    fi
    base="origin/${db}"
  fi

  # An existing path is either this exact worktree (idempotent re-run) or
  # something this must not touch. "Same branch name" is not identity: two
  # unrelated repos both have a `feat/x`, and treating one as provisioned from
  # the other would hand a worker somebody else's tree. Identity is the shared
  # object store — the common dir both sides resolve to.
  if [[ -e "$abs_path" ]]; then
    if ! git -C "$abs_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      warn "'${abs_path}' already exists and is not a git work tree — remove it or choose another path"
      return 2
    fi
    local shared_common="" path_common=""
    if ! shared_common="$(common_dir_of "$shared")"; then
      warn "cannot resolve the git common dir of ${shared}: $(tr '\n' ' ' < "$ERRFILE") — cannot establish which repository ${abs_path} would belong to"
      return 2
    fi
    if ! path_common="$(common_dir_of "$abs_path")"; then
      warn "cannot resolve the git common dir of ${abs_path}: $(tr '\n' ' ' < "$ERRFILE") — cannot tell which repository that work tree belongs to"
      return 2
    fi
    if [[ "$path_common" != "$shared_common" ]]; then
      warn "'${abs_path}' is a work tree of a DIFFERENT repository (its git dir is ${path_common}, this checkout's is ${shared_common}) — choose another path, or remove that work tree from the repository that owns it"
      return 2
    fi
    # A failing rev-parse is a tool failure, not a branch name. Collapsing it
    # into an empty string would report the work tree as being on '' — the
    # wrong branch — and send the operator to fix something that is not broken
    # (rules/error-handling.md Shell Error Handling).
    local on="" rc=0
    on="$(git -C "$abs_path" rev-parse --abbrev-ref HEAD 2>"$ERRFILE")" || rc=$?
    if (( rc != 0 )); then
      warn "\`git -C ${abs_path} rev-parse --abbrev-ref HEAD\` failed (exit ${rc}): $(tr '\n' ' ' < "$ERRFILE") — cannot tell which branch that work tree is on; inspect it by hand"
      return 2
    fi
    if [[ "$on" != "$branch" ]]; then
      warn "'${abs_path}' is a work tree on '${on}', not '${branch}' — choose another path, or remove it with \`git worktree remove\`"
      return 2
    fi
    jq -n --arg path "$abs_path" --arg branch "$branch" --arg base "$base" \
      '{path: $path, branch: $branch, base_ref: $base, state: "already-provisioned"}'
    return 0
  fi

  # Attach when the branch already exists on either side; create otherwise.
  local state="created" rc=0
  if git -C "$shared" show-ref --verify --quiet "refs/heads/${branch}"; then
    state="attached"
    git -C "$shared" worktree add "$abs_path" "$branch" >/dev/null 2>"$ERRFILE" || rc=$?
  elif git -C "$shared" show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    state="attached"
    git -C "$shared" worktree add --track -b "$branch" "$abs_path" "origin/${branch}" >/dev/null 2>"$ERRFILE" || rc=$?
  else
    git -C "$shared" worktree add -b "$branch" "$abs_path" "$base" >/dev/null 2>"$ERRFILE" || rc=$?
  fi
  if (( rc != 0 )); then
    warn "\`git worktree add\` failed (exit ${rc}) for ${abs_path} on ${branch}: $(tr '\n' ' ' < "$ERRFILE")"
    return 2
  fi

  jq -n --arg path "$abs_path" --arg branch "$branch" --arg base "$base" --arg state "$state" \
    '{path: $path, branch: $branch, base_ref: $base, state: $state}'
  return 0
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
