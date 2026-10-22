---
name: herdr-teamlead
description: >
  Run Herdr rounds with on-demand specialists, model tiers, bounded briefs,
  report verification, and release gates. Use for requests to dispatch the Herdr
  team, balance worker usage, collect reports, run or retrieve retrospectives,
  catch up on outstanding user attention, curate team lessons, or save and resume
  lead handoffs. Live rounds require HERDR_ENV; saved memory and attention work
  offline. Other standalone tasks skip this skill.
---

# Herdr Team Lead Skill

Process steps in order. Do not skip ahead.

Before any finish with enrolled work, reconcile the whole fleet under
`skills/herdr-teamlead/references/supervision.md`. Continue observation or persist an authorized pause
or handoff covering every active assignment. Keep user attention visible under
`skills/herdr-teamlead/references/attention.md`.

Read the team contract in full before Step 1; every step runs under it:

```text
skills/herdr-teamlead/references/team-operation.md
```

Each command resolves `CP` to the local or home plugin. Repeat its resolver in
every call. Prose `skills/...` paths are relative to that root.

## Step 1 — Determine the Mode

For catch-up or saved attention, follow `skills/herdr-teamlead/references/attention.md`. For lesson
curation, saved lead context, or a lead handoff, follow `skills/herdr-teamlead/references/working-memory.md`.
Use the recorded state override or default; these commands need no live Herdr.
Finish here after the requested operation. They grant no new task authority.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" catch-up [--state <state-file>]
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" memory-show [--state <state-file>] [--id <stow-id>]
```

Read all attention pages before claiming completeness. Use the referenced owner
commands for recording, resolution, lessons, and a new stow. Non-zero requires
reporting its diagnostic; never fabricate missing history.

For saved retrospective requests, use the recorded state override or default.
Read saved notes; report their date, coverage, conclusions, and path. Finish here.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" retro-list [--state <state-file>] [--task <task-id>] [--since <ISO-time>]
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" retro-show [--state <state-file>] [--id <retro-id>] [--task <task-id>]
```

Both emit JSON; non-zero requires reporting the diagnostic. `retro-show` defaults
to latest. For other requests, read `HERDR_ENV` before running scripts.

- **Unset or empty** — this skill does not apply. Say so and do the task
  directly, without roster calls, briefs, provisioning, reports, or simulated
  worker roles. Finish here.
- **Set, with a team task, new retrospective, edit, or review of existing
  code** — proceed immediately to Step 2. The lead never edits or reviews
  code itself, whatever the size.
- **Set, with a question or one bounded read-only lookup** — answer it
  directly and cite what you read: a file and line, or a command and its
  output. Edit nothing and dispatch nothing. Finish here.

## Step 2 — Run the Preflight

For an explicit retrospective request, complete a new retrospective under
`skills/herdr-teamlead/references/retrospectives.md` and finish here. Otherwise
run the `preflight` on every round start, resume, and return from a later step,
route its verdict, and record authority, ledger and staffing. Read
`skills/herdr-teamlead/references/round-setup.md` Step 2 first; it holds the command.

## Step 3 — Plan the Roles

Staff the fired triggers, validate any review partition, and plan the roles.
Read `skills/herdr-teamlead/references/round-setup.md` Step 3 first; it holds the commands.

## Step 4 — Build the Review Package

Reviewer and tester briefs need a review package bound to the recorded base
and pushed head. Read `skills/herdr-teamlead/references/round-setup.md` Step 4 first; it holds the command.

## Step 5 — Compose the Briefs

Resolve policy paths, write the values file, and compose. Read
`skills/herdr-teamlead/references/round-setup.md` Step 5 first; it holds the commands.

## Step 6 — Provision the Worktrees

Prune every round, then provision every worktree a brief names. Read
`skills/herdr-teamlead/references/round-setup.md` Step 6 first; it holds the commands.

## Step 7 — Label the Layout

Optional, once per team; skip an already named sidebar. Read
`skills/herdr-teamlead/references/round-setup.md` Step 7 first; it holds the command.

## Step 8 — Dispatch the Briefs

Send the planned briefs through `apply`. Read `skills/herdr-teamlead/references/round-dispatch.md` Step 8 first.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply [--config <config-file>] \
  --assignments <plan-file> \
  --brief <role>=<path> [--brief <role>=<path>]... \
  --report <role>=<report> [--report <role>=<report>]... \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --retain-specialist | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

## Step 9 — Observe the Fleet

Watch every enrolled assignment, then confirm each report's delivery. Read
`skills/herdr-teamlead/references/round-dispatch.md` Step 9 first.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" supervision-watch [--state <state-file>]
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/wait-report.sh" --once \
  [--worktree <worker-checkout>] [--base <dispatch-base>] \
  [--since <dispatch-sent-at>] <agent-name> <report-path>
```

## Step 10 — Gate the Round

Read every report in full, classify findings, and route the task to Step 2,
Step 11, Step 19 or Step 20. Read `skills/herdr-teamlead/references/round-dispatch.md` Step 10 first.

## Step 11 — Compose the Judge Brief

Optional. No judge trigger — proceed to Step 18. A dispute or an exhausted
allowance needs a judge; read `skills/herdr-teamlead/references/judge-round.md` before Steps 11–15 and 17.

## Step 12 — Re-measure the Shared Window

Run `measure` alone, not the preflight; `skills/herdr-teamlead/references/judge-round.md` Step 12 routes the result.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure
```

## Step 13 — Plan the Judge Seat

Plan the pinned judge against Step 12's snapshot under `skills/herdr-teamlead/references/judge-round.md` Step 13.

## Step 14 — Start the Judge Worker on Its Pinned Tier

An existing judge worker goes to Step 15. An empty shell pane starts under
`skills/herdr-teamlead/references/judge-round.md` Step 14:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/start-judge-worker.sh" \
  <step-13-plan-file> <pane> [claude|codex|grok] --task <task-id> \
  [--config <config-file>] [--state <state-file>]
```

## Step 15 — Dispatch the Judge

Dispatch the judge with Step 8's `apply` under `skills/herdr-teamlead/references/judge-round.md` Step 15.

## Step 16 — Wait for the Ruling

Run Step 9's fleet observation loop, including the judge named by Step 13.
Proceed immediately to Step 17 once its report lands; keep other enrollments
under observation.

## Step 17 — Act on the Ruling or Remedy

Route the ruling or remedy under `skills/herdr-teamlead/references/judge-round.md` Step 17.

## Step 18 — Release the Pull Request

Dispatch the release as a fresh `release` assignment and verify it. Read
`skills/herdr-teamlead/references/round-completion.md` Step 18 first.

## Step 19 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`, then run Step 6's prune script again
for the round's other worktrees. Proceed immediately to Step 20.

## Step 20 — Log the Round

Finalize the ledger, save progress and the lead's handoff, reconcile
supervision, and report. Read `skills/herdr-teamlead/references/round-completion.md` Step 20
first, then finish here.

For the daily standup, use
`Skill(skill: "herdr-standup")`.
