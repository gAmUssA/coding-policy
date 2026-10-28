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

Every step runs under the whole team contract. Read its core, every section
above Team Composition, before Step 1:

```text
skills/herdr-teamlead/references/team-operation.md Reading This Contract
```

Each step lists its step reference and contract sections to read before
acting on it.

Each command resolves `CP` to the local or home plugin. Repeat its resolver in
every call. Prose `skills/...` paths are relative to that root.

## Step 1 — Determine the Mode

For catch-up or saved attention, follow the attention reference. For lesson
curation, saved lead context, or a lead handoff, follow the working-memory
reference and the contract's Working Memory:

```text
skills/herdr-teamlead/references/attention.md
skills/herdr-teamlead/references/working-memory.md
skills/herdr-teamlead/references/team-operation.md Working Memory
```

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

For an explicit retrospective request, complete a new retrospective and
finish here:

```text
skills/herdr-teamlead/references/retrospectives.md
skills/herdr-teamlead/references/team-operation.md Retrospectives
```

Otherwise run the `preflight` on every round start, resume, and return from a
later step, route its verdict, and record authority, ledger and staffing. Read
first:

```text
skills/herdr-teamlead/references/round-setup.md Step 2
skills/herdr-teamlead/references/team-operation.md Task Ledger
skills/herdr-teamlead/references/team-operation.md Retrospectives
```

## Step 3 — Plan the Roles

Staff the fired triggers, validate any review partition, and plan the roles.
Read first:

```text
skills/herdr-teamlead/references/round-setup.md Step 3
skills/herdr-teamlead/references/team-operation.md Team Composition
skills/herdr-teamlead/references/team-operation.md Round Tiers
skills/herdr-teamlead/references/team-operation.md Review Before PR
```

A planned consultation also reads Specialist Consultations; a fix round also
reads Fix Loops:

```text
skills/herdr-teamlead/references/team-operation.md Specialist Consultations
skills/herdr-teamlead/references/team-operation.md Fix Loops
```

## Step 4 — Build the Review Package

Reviewer and tester briefs need a review package bound to the recorded base
and pushed head. Read first:

```text
skills/herdr-teamlead/references/round-setup.md Step 4
```

## Step 5 — Compose the Briefs

Resolve policy paths, write the values file, and compose. Read first:

```text
skills/herdr-teamlead/references/round-setup.md Step 5
skills/herdr-teamlead/references/team-operation.md Assignment Reasoning
skills/herdr-teamlead/references/team-operation.md Working Memory
```

## Step 6 — Provision the Worktrees

Prune every round, then provision every worktree a brief names. Read first:

```text
skills/herdr-teamlead/references/round-setup.md Step 6
skills/herdr-teamlead/references/team-operation.md Writers and Checkouts
```

## Step 7 — Label the Layout

Optional, once per team; skip an already named sidebar. Read first:

```text
skills/herdr-teamlead/references/round-setup.md Step 7
```

## Step 8 — Dispatch the Briefs

Send the planned briefs through `apply`. Read first:

```text
skills/herdr-teamlead/references/round-dispatch.md Step 8
skills/herdr-teamlead/references/team-operation.md Dispatch Safety
skills/herdr-teamlead/references/team-operation.md Worker Launch Mode
```

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
first:

```text
skills/herdr-teamlead/references/round-dispatch.md Step 9
```

A wait that ends in a stall also reads Stalled Workers:

```text
skills/herdr-teamlead/references/team-operation.md Stalled Workers
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" supervision-watch [--state <state-file>]
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/wait-report.sh" --once \
  [--state <state-file>] [--config <config-file>] \
  [--worktree <worker-checkout>] [--base <dispatch-base>] \
  [--since <dispatch-sent-at>] <agent-name> <report-path>
```

Pass the dispatch's `--state` and `--config` selections. `--state` selects
the launch receipts beside that state file; omission uses the teamlead state
default. `--config` selects the configured worker identity; omission reads
the default config when present. A configured pi seat requires its matching
receipt and config for native verification. Neither flag writes config.
The delivery JSON, exits and observation effects are documented in Step 9 of
`skills/herdr-teamlead/references/round-dispatch.md`; the script header owns
the complete contract.

## Step 10 — Gate the Round

Read every report in full, classify findings, and route the task to Step 2,
Step 11, Step 19 or Step 20. Read first:

```text
skills/herdr-teamlead/references/round-dispatch.md Step 10
```

Any blocking finding also reads Fix Loops:

```text
skills/herdr-teamlead/references/team-operation.md Fix Loops
```

## Step 11 — Compose the Judge Brief

Optional. No judge trigger — proceed to Step 18. A dispute or an exhausted
allowance needs a judge; read these before Steps 11–15 and 17:

```text
skills/herdr-teamlead/references/judge-round.md
skills/herdr-teamlead/references/team-operation.md Judge Seat
```

## Step 12 — Re-measure the Shared Window

Run `measure` alone, not the preflight; the step reference routes the result:

```text
skills/herdr-teamlead/references/judge-round.md Step 12
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure
```

## Step 13 — Plan the Judge Seat

Plan the pinned judge against Step 12's snapshot under:

```text
skills/herdr-teamlead/references/judge-round.md Step 13
```

## Step 14 — Start the Judge Worker on Its Pinned Tier

An existing judge worker goes to Step 15. An empty shell pane starts under:

```text
skills/herdr-teamlead/references/judge-round.md Step 14
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/start-judge-worker.sh" \
  <step-13-plan-file> <pane> [claude|codex|grok] --task <task-id> \
  [--config <config-file>] [--state <state-file>]
```

## Step 15 — Dispatch the Judge

Dispatch the judge with Step 8's `apply` under:

```text
skills/herdr-teamlead/references/judge-round.md Step 15
```

## Step 16 — Wait for the Ruling

Run Step 9's fleet observation loop, including the judge named by Step 13.
Proceed immediately to Step 17 once its report lands; keep other enrollments
under observation.

## Step 17 — Act on the Ruling or Remedy

Route the ruling or remedy under:

```text
skills/herdr-teamlead/references/judge-round.md Step 17
```

## Step 18 — Release the Pull Request

Dispatch the release as a fresh `release` assignment and verify it. Read first:

```text
skills/herdr-teamlead/references/round-completion.md Step 18
```

## Step 19 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`, then run Step 6's prune script again
for the round's other worktrees. Proceed immediately to Step 20.

## Step 20 — Log the Round

Finalize the ledger, save progress and the lead's handoff, reconcile
supervision, and report. Read first, then finish here:

```text
skills/herdr-teamlead/references/round-completion.md Step 20
```

For the daily standup, use
`Skill(skill: "herdr-standup")`.
