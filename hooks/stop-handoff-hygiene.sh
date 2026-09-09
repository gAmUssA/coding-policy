#!/usr/bin/env bash
# Flag leftover local hygiene before the agent hands off (a Stop gate).
#
# A Claude Code `Stop` hook: at handoff it runs deterministic, universal
# git-hygiene checks and, when it finds clearly-actionable leftovers, blocks the
# stop once with an actionable reason so the agent can clean up. We hand-cleaned
# exactly this mess in real sessions — stray merged-and-remote-deleted local
# branches and an abandoned worktree. This mechanizes the pre-handoff cleanup
# rules/language-diagnostics.md endorses ("a Stop or pre-handoff hook running the
# gate mechanizes this") and complements the fleet-wide delete_branch_on_merge
# (that auto-cleans REMOTE branches; this covers the LOCAL branches/worktrees it
# never touches).
#
# Why nativeHooks (not the portable `hooks` tier): blocking a stop is an
# agent-specific contract with no portable consensus form — the `hooks` tier
# wraps the script in `tessl hook run`, which translates `additionalContext`
# (informational), not a stop-block. nativeHooks writes the entry raw to each
# agent's native config, so the agent reads the script's `{"decision":"block"}`
# directly. Claude Code and Codex share the same Stop contract (decision/reason/
# stop_hook_active), so the SAME script is dual-wired. The two entries differ in
# shape by necessity: Claude Code's config takes command + args[], Codex's takes
# a single command string (its config has no args field), so nativeHooks.codex
# uses `bash "<path>"` as one string. Verified by installing into scratch
# consumers and inspecting .claude/settings.json and .codex/config.toml.
#
# Blocking findings (gate the stop, once):
#   - Leftover local branches whose upstream is gone (merged then remote-deleted).
#   - Orphaned linked worktrees whose branch's upstream is gone.
#   - Diagnostics findings in the CHANGED set only (uncommitted .sh/.py):
#     lint the .sh with shellcheck, the .py with pyright. Skipped when nothing
#     lintable changed, so a clean handoff costs nothing. An absent engine is
#     blocking (the gate can't clear findings without it) — install and re-check.
#     Inlined here rather than delegated to scripts/run-diagnostics.sh, which the
#     Tessl packer does not ship (only rules/, skills/, hooks/ surfaces publish).
#     Python uses the first executable interpreter in VIRTUAL_ENV, the repo's
#     .venv, then venv, and passes it as --pythonpath. Prefer that environment's
#     pyright when installed; otherwise use PATH. With no environment, retain
#     Pyright's own configuration/default interpreter resolution. Import errors
#     remain blocking; an environment correction never suppresses diagnostics.
# Report-only (never blocks on its own): a dirty working tree — often intentional
#   work-in-progress, surfaced to the user but not trapped.
#
# Loop-safe: reads `stop_hook_active` from stdin and allows immediately when set,
# so it blocks at most once per handoff chain — never traps the agent in a loop.
# Fail-open: any inability to evaluate (no jq, not a git repo, parse failure)
# allows the stop. A hygiene nudge must never wedge a handoff.
#
# Contract:
#   stdin : Claude Code Stop JSON. Only `.stop_hook_active` is read.
#   stdout: on a block, one JSON object {"decision":"block","reason":"<text>"};
#           otherwise nothing.
#   exit  : always 0 (block is expressed in stdout JSON, never via exit code).
#           Best-effort failures warn to stderr and allow the stop.
#   state : none — every check reads live git state.
set -euo pipefail

warn() { printf 'stop-handoff-hygiene: %s\n' "$1" >&2; }

# Membership test without associative arrays (macOS ships bash 3.2).
in_list() { # <needle> <haystack...>
  local needle="$1" x; shift
  for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done
  return 1
}

# Is this session a Herdr WORKER rather than the lead?
#
# The team rules reserve the shared checkout and every worktree operation for
# the lead: a worker "runs no git command against the shared checkout,
# mutating or otherwise" and "never creates, moves, or removes a worktree"
# (rules/agent-team-operation.md Writers and Checkouts). A hook that tells a
# worker to fast-forward `main` or remove a worktree is instructing it to
# break that rule -- which is exactly what happened in a live round, where the
# worker reported the contradiction and then obeyed the hook.
#
# Herdr exports no lead/worker flag, so the role is derived from where the
# session sits: the lead works in the shared checkout, every worker works in a
# linked worktree. In a linked worktree `--git-dir` and `--git-common-dir`
# resolve differently; in the main checkout they are the same.
#
# 0 = a Herdr worker (suppress lead-only advice), 1 = the lead, a standalone
# agent, or anything this cannot determine. Fail open: a hook that goes silent
# because a git command failed would be worse than one that speaks up.
is_herdr_worker() {
  [[ -n "${HERDR_ENV:-}" ]] || return 1

  local git_dir common_dir rc=0
  git_dir="$(git rev-parse --absolute-git-dir 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "git rev-parse --absolute-git-dir failed (exit ${rc}) — cannot tell a Herdr worker from the lead; treating this as the lead"
    return 1
  fi
  rc=0
  common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    warn "git rev-parse --git-common-dir failed (exit ${rc}) — cannot tell a Herdr worker from the lead; treating this as the lead"
    return 1
  fi

  [[ "$git_dir" != "$common_dir" ]]
}

main() {
  local input active inside
  local -a gone_branches=() wt_paths=() wt_branches=() leftover=() orphaned=() changed=()
  local -a blocking=() reports=()

  # jq is required to read stop_hook_active and to emit the block JSON safely.
  # Without it we cannot evaluate loop-safety, so fail open (allow the stop).
  if ! command -v jq >/dev/null 2>&1; then
    warn "jq not found — install jq to enable the handoff-hygiene gate; allowing stop"
    return 0
  fi

  # Read the Stop payload and honor the loop guard: if this stop is already the
  # result of our prior block, allow it — never block twice (no loop).
  input="$(cat)" || input=""
  active="$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null)" || active="parse-error"
  [[ "$active" == "true" ]] && return 0
  [[ "$active" == "parse-error" ]] && { warn "could not parse Stop payload — allowing stop"; return 0; }

  command -v git >/dev/null 2>&1 || { warn "git not found — allowing stop"; return 0; }

  # Outside a work tree there is nothing to check. `--is-inside-work-tree` prints
  # true/false and exits 0 inside any repo; exit 128 is the expected "not a git
  # repository" non-result (silent allow). Any other exit is a real failure and
  # is surfaced before failing open (rules/error-handling.md). A bare repo /
  # gitdir ("false") also allows silently.
  rc=0
  inside="$(git rev-parse --is-inside-work-tree 2>/dev/null)" || rc=$?
  if (( rc != 0 )); then
    (( rc == 128 )) || warn "git rev-parse --is-inside-work-tree failed (exit ${rc}) — allowing stop"
    return 0
  fi
  [[ "$inside" == "true" ]] || return 0

  # Branch and worktree cleanup is the lead's, never a worker's
  # (rules/agent-team-operation.md Writers and Checkouts). Blocking a worker's
  # stop over leftovers it is forbidden to remove would force it to either
  # disobey the rule or fail to hand off. The lead's own teardown runs at the
  # end of its round.
  #
  # ONLY that cleanup is suppressed. The diagnostics gate and the dirty-tree
  # report apply to a worker's own changed files, which are its to fix
  # (rules/language-diagnostics.md Gate It Deterministically) -- skipping them
  # here would let a worker hand off findings nobody else is going to see.
  if ! is_herdr_worker; then
    collect_gone_branches
    collect_worktrees

    # Partition gone branches: those checked out in a linked worktree are
    # reported as orphaned worktrees (remove the worktree); the rest as
    # leftover branches.
    local b p i
    for b in ${gone_branches[@]+"${gone_branches[@]}"}; do
      in_list "$b" ${wt_branches[@]+"${wt_branches[@]}"} || leftover+=("$b")
    done
    for (( i = 0; i < ${#wt_paths[@]}; i++ )); do
      b="${wt_branches[$i]}"; p="${wt_paths[$i]}"
      if in_list "$b" ${gone_branches[@]+"${gone_branches[@]}"}; then orphaned+=("${p} (branch ${b})"); fi
    done

    build_branch_findings
  fi

  run_changed_diagnostics
  check_dirty_tree

  if (( ${#blocking[@]} > 0 )); then
    emit_block
  elif (( ${#reports[@]} > 0 )); then
    # Report-only: surface to the user (stderr) but do not block the handoff.
    local r
    for r in "${reports[@]}"; do warn "$r"; done
  fi
  return 0
}

# Populate gone_branches with local branches whose upstream is [gone]. Capture
# for-each-ref's output and exit status so a failure is surfaced, not silently
# read as "no leftovers" (rules/error-handling.md).
collect_gone_branches() {
  local out rc=0 name track
  out="$(git for-each-ref --format='%(refname:short)%09%(upstream:track)' refs/heads)" || rc=$?
  if (( rc != 0 )); then
    warn "git for-each-ref failed (exit ${rc}) — skipping the leftover-branch check"
    return 0
  fi
  while IFS=$'\t' read -r name track; do
    [[ "$track" == "[gone]" ]] && gone_branches+=("$name")
  done <<< "$out"
  return 0
}

# Populate wt_paths/wt_branches for LINKED worktrees only (the first porcelain
# record is the main worktree and is skipped). Detached worktrees have no branch
# and are skipped.
collect_worktrees() {
  local out rc=0 line key val cur_path="" cur_branch="" first=1
  out="$(git worktree list --porcelain)" || rc=$?
  if (( rc != 0 )); then
    warn "git worktree list failed (exit ${rc}) — skipping the orphaned-worktree check"
    return 0
  fi
  flush() {
    if (( first )); then first=0
    elif [[ -n "$cur_branch" ]]; then wt_paths+=("$cur_path"); wt_branches+=("$cur_branch"); fi
    cur_path=""; cur_branch=""
  }
  while IFS= read -r line; do
    if [[ -z "$line" ]]; then flush; continue; fi
    key="${line%% *}"; val="${line#* }"
    case "$key" in
      worktree) cur_path="$val" ;;
      branch)   cur_branch="${val#refs/heads/}" ;;
    esac
  done <<< "$out"
  [[ -n "$cur_path" ]] && flush   # flush a trailing record with no blank line
  return 0
}

# Turn leftover branches and orphaned worktrees into blocking-finding sections.
build_branch_findings() {
  local current b p section rc=0
  # symbolic-ref exits 1 for the expected detached-HEAD non-result (no current
  # branch); any other exit is a real failure and is surfaced.
  current="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)" || rc=$?
  (( rc == 0 )) || current=""
  (( rc == 0 || rc == 1 )) || warn "git symbolic-ref HEAD failed (exit ${rc})"
  if (( ${#leftover[@]} > 0 )); then
    section="Leftover local branches (merged, upstream deleted) — delete them:"
    for b in "${leftover[@]}"; do
      if [[ "$b" == "$current" ]]; then
        section+=$'\n'"  - ${b} (currently checked out — switch away first): git switch <other> && git branch -d ${b}"
      else
        section+=$'\n'"  - ${b}: git branch -d ${b}"
      fi
    done
    blocking+=("$section")
  fi
  if (( ${#orphaned[@]} > 0 )); then
    section="Orphaned worktrees (branch merged, upstream deleted) — remove them:"
    for p in "${orphaned[@]}"; do
      section+=$'\n'"  - ${p}: git worktree remove <path> && git branch -d <branch>"
    done
    blocking+=("$section")
  fi
  return 0
}

# Diagnostics on the CHANGED set only: uncommitted .sh/.py files. Skips silently
# when nothing lintable changed. shellcheck the .sh, pyright the .py; findings
# block. A required engine being absent is also blocking (rules/language-
# diagnostics.md Install, Don't Skip — the gate cannot clear findings without it).
run_changed_diagnostics() {
  local repo_root
  if ! repo_root="$(git rev-parse --show-toplevel)"; then
    warn "cannot locate the worktree root — restore repository access and re-run the diagnostics gate"
    return 0
  fi
  if ! cd "$repo_root"; then
    warn "cannot enter ${repo_root} — restore worktree access and re-run the diagnostics gate"
    return 0
  fi
  collect_changed_lintable
  (( ${#changed[@]} > 0 )) || return 0

  local f out
  local -a sh_files=() py_files=()
  for f in "${changed[@]}"; do
    case "$f" in
      *.sh) sh_files+=("$f") ;;
      *.py) py_files+=("$f") ;;
    esac
  done

  if (( ${#sh_files[@]} > 0 )); then
    if command -v shellcheck >/dev/null 2>&1; then
      if ! out="$(shellcheck "${sh_files[@]}" 2>&1)"; then
        blocking+=("shellcheck findings in changed shell files — fix before handoff:"$'\n'"${out}")
      fi
    else
      blocking+=("shellcheck is not installed but changed .sh files need checking — install shellcheck to clear the pre-handoff diagnostics gate (rules/language-diagnostics.md).")
    fi
  fi

  if (( ${#py_files[@]} > 0 )); then
    local py_interp="" pyright_bin="" env_dir candidate
    local -a py_args=()
    for env_dir in "${VIRTUAL_ENV:-}" "$repo_root/.venv" "$repo_root/venv"; do
      [[ -n "$env_dir" ]] || continue
      for candidate in "$env_dir/bin/python" "$env_dir/Scripts/python.exe"; do
        if [[ -f "$candidate" && -x "$candidate" ]]; then
          py_interp="$candidate"
          break
        fi
      done
      [[ -z "$py_interp" ]] || break
    done
    if [[ -n "$py_interp" ]]; then
      py_args=(--pythonpath "$py_interp")
      for candidate in "${py_interp%/*}/pyright" "${py_interp%/*}/pyright.exe"; do
        if [[ -f "$candidate" && -x "$candidate" ]]; then pyright_bin="$candidate"; break; fi
      done
    fi
    if [[ -z "$pyright_bin" ]]; then
      pyright_bin="$(command -v pyright)" || pyright_bin=""
    fi
    if [[ -n "$pyright_bin" ]]; then
      if ! out="$("$pyright_bin" ${py_args[@]+"${py_args[@]}"} "${py_files[@]}" 2>&1)"; then
        blocking+=("pyright findings in changed Python files — check the project environment and fix before handoff (engine: ${pyright_bin}; interpreter: ${py_interp:-Pyright default/config}):"$'\n'"${out}")
      fi
    else
      blocking+=("pyright is not installed but changed .py files need checking — install pyright to clear the pre-handoff diagnostics gate (rules/language-diagnostics.md).")
    fi
  fi
  return 0
}

# Populate `changed` with uncommitted .sh/.py files: tracked changes vs HEAD (or
# the index in an unborn repo) plus untracked files, NUL-safe.
collect_changed_lintable() {
  # NUL-safe capture via a temp file so the producer's exit status stays
  # observable (a process substitution would hide it); warn on failure and fall
  # open to whatever was collected (rules/error-handling.md).
  local f tmp rc=0
  tmp="$(mktemp)" || { warn "mktemp failed — skipping changed-set diagnostics"; return 0; }

  rc=0
  if git rev-parse --verify -q HEAD >/dev/null 2>&1; then
    git diff --name-only -z --diff-filter=ACMR HEAD -- '*.sh' '*.py' > "$tmp" || rc=$?
  else
    git diff --name-only -z --diff-filter=ACMR --cached -- '*.sh' '*.py' > "$tmp" || rc=$?
  fi
  (( rc == 0 )) || warn "git diff failed (exit ${rc}) — changed-set diagnostics may be incomplete"
  while IFS= read -r -d '' f; do changed+=("$f"); done < "$tmp"

  rc=0
  git ls-files -z --others --exclude-standard -- '*.sh' '*.py' > "$tmp" || rc=$?
  (( rc == 0 )) || warn "git ls-files failed (exit ${rc}) — untracked changes may be missed"
  while IFS= read -r -d '' f; do changed+=("$f"); done < "$tmp"

  rm -f "$tmp" || warn "could not remove temp file ${tmp}"
  return 0
}

# A dirty working tree is report-only — often intentional WIP, never blocks alone.
check_dirty_tree() {
  local status rc=0
  status="$(git status --porcelain)" || rc=$?
  if (( rc != 0 )); then
    warn "git status failed (exit ${rc}) — skipping the dirty-tree report"
    return 0
  fi
  [[ -n "$status" ]] && reports+=("Working tree has uncommitted changes — commit, stash, or discard before handoff.")
  return 0
}

# Emit Claude Code's native Stop block decision with the assembled reason.
emit_block() {
  local reason section
  reason="Pre-handoff hygiene — resolve these local leftovers before finishing:"
  for section in "${blocking[@]}"; do reason+=$'\n\n'"${section}"; done
  if (( ${#reports[@]} > 0 )); then
    local r
    for r in "${reports[@]}"; do reason+=$'\n\n'"${r}"; done
  fi
  jq -n --arg r "$reason" '{decision: "block", reason: $r}' ||
    warn "could not emit the block decision as JSON — allowing stop"
  return 0
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
