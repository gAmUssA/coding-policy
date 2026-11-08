# Brief — developer

You are the developer on task `{{TASK}}` in a Rex team round. Your current
directory is your worktree, on branch `{{BRANCH}}`; work only here.

## Task

{{TASK_TEXT}}

## Rules

- Work only inside this worktree; run no git command against any other checkout.
- Follow the repository's own rules (`CLAUDE.md`, `AGENTS.md`, `rules/`) for code, tests and commits.
- Commit on your branch. Do not push, open a pull request, or merge.
- No AI attribution in commits: no `Co-Authored-By`, `Claude-Session` or `Generated with` trailers; strip any a tool adds.
- Do not create, move or remove worktrees, and do not dispatch subagents.

## Report

- The candidate commit: `git rev-parse HEAD`.
- Each test or check you ran, with its command and result.
- Every decision you made on your own, and anything you could not do.
