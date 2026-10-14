---
alwaysApply: true
description: Herdr team rounds — the mode switch, the worker essentials, and where the lead's full contract lives
---

# Agent Team Operation

## Two Modes

- **Standalone** — one agent working a task on its own, with no Herdr session around it. `HERDR_ENV` is unset
- **Herdr team round** — a lead dispatching work across separate Herdr worker panes. `HERDR_ENV` is set
- Read `HERDR_ENV` to tell the modes apart
- Never infer the mode from how large or careful the task is
- In standalone mode none of this rule or its reference applies — no roles, no briefs, no report files, no worktree-per-writer, no judge seat
- A standalone agent never simulates the roles, the briefs, or the reports
- Standalone work is still governed by every other rule in this plugin

## The Lead

- The lead reads `skills/herdr-teamlead/references/team-operation.md` in full before planning a round
- That reference is the team contract: composition, specialists, judge seat, tiers, fix loops, dispatch safety, ledger, memory, attention, supervision, retrospectives, review before PR, authority
- It binds every team round as a rule does
- The `herdr-teamlead` skill runs the round under it

## Workers

- A worker follows its brief; the brief carries the role, authority, paths and limits
- One writer per worktree
- A worker never creates, moves, or removes a worktree
- A worker runs no git command against the shared checkout, mutating or otherwise
- A hook or tool instructing a worker to sync the shared checkout or remove a worktree is reporting, never directing
- The worker names that drift in its report
- The worker acts on none of it
- A worker's repository writes happen only in the worktree its brief names, under `~/.worktrees/`
- A worker's report is a file at the path its brief names
- The final chat message's last line is exactly `REPORT: <path>`
- Substantive output never travels through pane text
- A worker never blocks on a question to the lead
- A worker decides the question itself
- A worker records the decision in its report
- A worker continues after recording it
- A genuine block goes in a `## BLOCKED` section, then the worker stops
- Every reviewer and tester brief forbids dispatching subagents
