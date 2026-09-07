---
alwaysApply: true
---

# Agent Worktree Isolation

## When to Isolate

- Any task that writes to the repo AND may run concurrently with another agent — branch work, file edits, scripted refactors, dependency upgrades, releases
- Read-only inspection on the shared checkout is permitted: `Grep`, `Read`, `Glob`, non-mutating `Bash` like `git status`
- Isolation becomes mandatory the moment the task crosses into mutating tools: `Edit`, `Write`, side-effecting `Bash`, branch creation
- Single-machine single-agent workflows may use worktrees but are not required to

## How to Isolate

- "Worktree" here means an additional working tree created via `git worktree add` — distinct from the base checkout, on its own branch, sharing the same `.git` object store
- Agent tool's `isolation: "worktree"` parameter is the canonical mechanism for spawned subagents — it provisions a fresh worktree and cleans up on exit if the agent made no changes
- For non-agent parallel work or human-launched second sessions, use `git worktree add -b <task-branch> ../<repo>-<task>` to create an isolated checkout on a new branch (or `git worktree add ../<repo>-<task> <existing-branch>` to attach to one that already exists), then `cd` in before any mutating operation

## Cleanup

- A worktree's lifecycle ends when its branch merges or is abandoned — remove it at that point; do not leave orphans in `git worktree list`
- Use `git worktree remove <path>`; never `rm -rf` the directory
- When the worktree's branch lands via `skills/release/SKILL.md` Step 7, the post-merge order is mandatory: `cd` back to the base checkout → fast-forward base `main` → `git worktree remove <worktree-path>` → `git branch -d <branch>`. Teardown precedes branch delete
- `hooks/stop-handoff-hygiene.sh` blocks the handoff once on an orphaned worktree

## Exception — Single-Reader Inspection

- Read-only inspection on the main checkout is permitted even with other agents active
- The exception evaporates the moment the inspection turns into "let me fix this one thing" — isolate first, mutate inside the worktree
