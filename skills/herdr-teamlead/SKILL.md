---
name: herdr-teamlead
description: >
  Run Herdr rounds with headroom-driven roles, qualified tiers, fresh briefs,
  report verification, and release gates. Use for requests to dispatch the Herdr
  team, balance worker usage, collect reports, run or retrieve retrospectives,
  catch up on outstanding user attention, curate team lessons, or save and resume
  lead handoffs. Live rounds require HERDR_ENV; saved memory and attention work
  offline. Other standalone tasks skip this skill.
---

# Herdr Team Lead Skill

Process steps in order. Do not skip ahead.

Before any finish with enrolled work, reconcile the whole fleet under
`references/supervision.md`. Continue observation or persist an authorized pause
or handoff covering every active assignment. Keep user attention visible under
`references/attention.md`.

Follow `rules/agent-team-operation.md` for round constraints.

Each command block resolves `CP` to the project-local plugin, falling back to
`$HOME/.tessl/plugins/gamussa/coding-policy`. Run the resolver in every call.
Prose `skills/...` paths are relative to that plugin root.

References:

```text
skills/herdr-teamlead/references/herdr.md
skills/herdr-teamlead/references/round-flow.md
skills/herdr-teamlead/references/round-setup.md
skills/herdr-teamlead/references/task-ledger.md
skills/herdr-teamlead/references/retrospectives.md
skills/herdr-teamlead/references/working-memory.md
skills/herdr-teamlead/references/attention.md
skills/herdr-teamlead/references/supervision.md
skills/herdr-teamlead/references/assignment-reasoning.md
skills/herdr-teamlead/state-schema.md
```

## Step 1 — Determine the Mode

For catch-up or saved attention, follow `references/attention.md`. For lesson
curation, saved lead context, or a lead handoff, follow `references/working-memory.md`.
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
- **Set, with a team task or new retrospective** — proceed immediately to Step 2.
- **Set, with a single edit, question, lookup, or existing-code review** — do
  it directly. Finish here.

## Step 2 — Verify Herdr and the Roster

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Emits the caller and live workers with kind, pane, and state.

- **Exit 0, agents present** — proceed to Step 3.
- **Exit 0, empty roster** — report unnamed panes from `herdr agent list` and
  the correcting `herdr agent rename <pane-id> <name>` command. Finish here.
- **Exit 1 or 2** — report the diagnostic verbatim and finish here.

If roles lack workers, name one or record combined roles in a single brief.
Never duplicate dispatch targets.
Start workers in YOLO mode under `references/model-tiers.md`; preserve it on
relaunch. Verify live permission flags before dispatch, including existing workers.

## Step 3 — Verify Authority for the Repo

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/verify-authority.sh" <owner/repo>
```

Record the emitted namespace ownership evidence using Step 3 of
`references/round-setup.md`. For a non-owned repo, reuse explicit per-action
operator permission; absent permission, remain read-only or finish here.
On non-zero, report the diagnostic and finish here.

Record the task's existing source and words in `TASK_AUTHORIZATION`,
and permitted actions and repo in `AUTHORIZED_ACTIONS`. Read-only uses `none`.
Ownership never expands task scope. Examples: `references/round-setup.md`.
Create or resume the stable task ledger under `references/task-ledger.md`.
Record its absolute path with the task authorization before the first dispatch.
Apply the round-setup reference's accepted-behavior, resume, and supervision
binding requirements before continuing.
Proceed immediately to Step 4.

## Step 4 — Measure Headroom

Run `references/retrospectives.md` on resume, before planning, or for an explicit
retrospective request. For an explicit request, complete a new retrospective and
finish here; otherwise continue below.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure
```

Emits and saves headroom, windows, state, `tier_billing`, and `failed_agents`.
Busy workers are skipped. Unmeasured billing stays `unknown`. Report failed
measurements and obtain their readings before relying on those seats.

Usage and `--trace` contracts:

```text
skills/herdr-teamlead/references/round-setup.md
```

Proceed immediately to Step 5 once the required readings are available.

## Step 5 — Plan the Roles

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles developer,tester,reviewer \
  [--exclude <role>=<agent>[,<agent>...]]... \
  [--round <role>=<round-type>] [--round-context <evidence.json>] \
  --task <task-id> [--fix-round <N>] [--correction-plan <id> --work <work.json>]
```

Emits the role plan without worker contact. On exit 1, resolve the diagnostic
before continuing. Apply the Step 5 constraints in `references/round-setup.md`:
exclude the author from verification, reserve the developer through early fixes,
preserve task identity and fix count, and reuse recorded correction bounds.
Operator-controlled tier and qualification contracts:

```text
skills/herdr-teamlead/references/model-tiers.md
skills/herdr-teamlead/references/dispatch-recovery.md
```

`--preview-tiers` authorizes no dispatch. Save the plan and rationale.
Proceed immediately to Step 6.

## Step 6 — Build the Review Package

For reviewer/tester briefs, run from a checkout holding the recorded commits:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/review-package.sh" \
  <recorded-base-sha> <pushed-head-sha> <round-reports-dir>/review-<base7>..<head7>.diff
```

Apply the Step 6 base, range, and rebuild requirements in
`references/round-setup.md`. Success prints the absolute review-package path;
set it as `REVIEW_PACKAGE`. On non-zero, fix the diagnostic and retry before
composing verification briefs. Other roles need no package.
Proceed immediately to Step 7.

## Step 7 — Compose the Briefs

Resolve policy paths through the Step 7 reference first. Write its outputs in
`shared` within `{"shared": {...}, "roles": {"<role>": {...}}}` and run:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/compose-briefs.sh" \
  "$CP/skills/herdr-teamlead/templates" \
  <values.json> <round-reports-dir>
```

Emits common and role-brief paths. On non-zero, fix the diagnostic before
dispatch. Validates composition inputs and review evidence before writing.
Use a fresh absolute report path per role and attempt.

Populate the shared and role-specific values under this reference's Step 7
contract, including the authority and review evidence from earlier steps:

```text
skills/herdr-teamlead/references/round-setup.md
```

Apply the Step 7 reference's brief-completeness requirements.
Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

Run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, base, and `created|attached|already-provisioned`. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only Phase 1 reviewers need none. Clean up after merge per
`rules/agent-worktree-isolation.md`. Proceed immediately to Step 9.

## Step 9 — Label the Layout

Optional, once per team; skip an already named sidebar.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/label-workspaces.sh" \
  <lead-label> [<agent>=<workspace-id>]...
```

Emits per-target `renamed|unchanged|failed`; exit 3 names partial failures.
Report label failures and continue. Proceed immediately to Step 10.

## Step 10 — Dispatch the Briefs

Complete the retrospective reference's cadence and transition checks before live
dispatch. Apply rechecks coverage before worker input. Dry runs prove no coverage.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply \
  --assignments <plan-file> \
  --brief developer=<path> --brief tester=<path> --brief reviewer=<path> \
  --report developer=<report> --report tester=<report> --report reviewer=<report> \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

Emits per-role JSON with clear/session evidence, task, fix number, verified
tier, and delivery status. Labelled dispatches carry `dispatch_id` and any
`context_transition`; fields are documented in `skills/herdr-teamlead/state-schema.md`.
Supply each role's exact fresh absolute report path from its brief. Apply enrolls
the assignment before worker input; unknown sends remain observation obligations.
Classify every brief against Step 3's authorization before sending it.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the recovery reference's Dispatch context requirements before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone; other roles clear separately. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

Follow the Dispatch Results contract in `references/round-flow.md` for busy,
uncertain, failed, and dry-run outcomes. Preserve all already enrolled work.

Read the context, recovery, and executable refusal contracts:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
skills/herdr-teamlead/references/model-tiers.md
```

Proceed to Step 11 with the dispatched roles.

## Step 11 — Observe the Fleet

Run the bounded foreground watcher for all enrolled assignments:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" supervision-watch [--state <state-file>]
```

Retain and await its real execution handle. The JSON result gives `reason`,
`through`, and durable `events`; a quiet deadline completes only that checkpoint.
For each event or pending recheck, verify report delivery:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/wait-report.sh" --once <agent-name> <report-path>
```

The checkpoint emits delivery JSON; exit 2 emits only stderr. Exit 0 confirms
delivery, 1 remains pending, 3 confirms blocked, 4 lacks confirmed delivery, and
5 proves terminal refusal. Read delivered reports in full. Preserve the
blocked/refusal and native-recovery paths in the following references; never
re-dispatch over uncertainty or automatically retry a provider refusal.

```text
skills/herdr-teamlead/references/supervision.md
skills/herdr-teamlead/references/dispatch-recovery.md
```

Record each outcome in the task ledger and user-facing obligations in the
attention queue. Acknowledge only handled event IDs through the saved snapshot;
schedule pending rechecks. Resolve enrollment only after recording its assessed
outcome, separately from assignment acceptance and task completion. Complete due
retrospectives between checkpoints without interrupting workers. Resume the fleet
watch while any observation obligation remains; one blocked worker never hides
another worker's report. Proceed to Step 12 when the required reports are delivered
or their unavailability and recovery are recorded.

## Step 12 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.
Record assignment acceptance or outstanding work in the task ledger against
the inspected report and artifact evidence. Record the task's gate decision
separately; a worker finishing its brief never completes the whole task.
Assess correction scope and bug evidence under `references/assignment-reasoning.md`.
Persist user-facing obligations under `references/attention.md` before presenting
them; record an actual answer or resolution separately from showing the item.

For an investigation-only task, assess every assigned report against the requested
knowledge deliverable. Resolve blocking findings through the same bounded and
judge paths below. Once its criteria hold, present the findings and preserve open
user decisions; proceed to Step 21 if a task worktree needs cleanup, otherwise
Step 22. No implementation or release is inferred from the diagnostic result.

- **Any blocking finding** — apply the round-flow reference's Blocking Gate
  contract and `rules/agent-team-operation.md` Fix Loops. Return to Step 4 for
  an authorized correction or Step 13 for a required judge ruling.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

Apply the release gate in this reference; obtain broad independent reviewer and
tester passes against the current pushed tip before release:

```text
skills/herdr-teamlead/references/round-flow.md
```

With its release criteria met, proceed immediately to Step 13.

## Step 13 — Compose the Judge Brief

Optional. Triggers and the ruling contract are in
`skills/herdr-teamlead/references/round-flow.md` "The Judge" (a bot
disagreement inside Step 20 returns here first). No trigger — proceed to
Step 20.

Compose the brief from `templates/brief-judge.md` through Step 7: the
dispute, both positions with report paths, the governing rule, the tree. Skip
Step 8 for the read-only judge. Proceed immediately to Step 14.

## Step 14 — Re-measure the Shared Window

Re-run Step 4's `measure` under its outcome contract. Resolve any unreadable
judge window before planning. Pass the fresh snapshot to Step 15; never reuse
the earlier reading as affordability proof. Proceed immediately to Step 15.

## Step 15 — Plan the Judge Seat

Plan the pinned judge against Step 14's fresh snapshot:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles judge --snapshot <step-14-measure-output> --task <task-id>
```

Exit 0 names the judge worker; proceed immediately to Step 16. On non-zero,
report the diagnostic and finish here. Never substitute a judge, lower its tier,
or hand-write an assignment to bypass the refusal.

## Step 16 — Start the Judge Worker on Its Pinned Tier

For an existing judge worker, proceed to Step 17 with a clearing dispatch.
For an empty shell pane, complete retrospective checks for the start and run:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/start-judge-worker.sh" \
  <step-15-plan-file> <pane> [claude|codex|grok] --task <task-id> [--state <state-file>]
```

Starts the pinned judge and verifies launch argv. The header owns the contract.

- **Exit 0** — proceed immediately to Step 17 with `--no-clear`.
- **Any non-zero** — report the diagnostic and finish here without briefing
  the worker or overriding its tier.

## Step 17 — Dispatch the Judge

Use Step 15's plan under Step 10's dispatch contract:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" apply \
  --assignments <plan-file> \
  --brief judge=<round>-judge.md --report judge=<absolute-report-path> \
  --common <path-to-COMMON.md> \
  --task <task-id> [--no-clear]
```

Step 10's outcomes govern. Use `--no-clear` only for the worker just started in
Step 16; an existing judge receives the default cleared relaunch with retrospective
coverage. Apply verifies the live tier before input. Proceed immediately to Step 18.

## Step 18 — Wait for the Ruling

Run Step 11's fleet observation loop, including the judge named by Step 15.
Proceed immediately to Step 19 once its report lands; keep other enrollments
under observation.

## Step 19 — Act on the Ruling

Apply the Ruling Outcomes contract in `references/round-flow.md`. Investigation
rulings return to Step 12's knowledge gate. Implementation rulings route
unchanged-branch rulings to verified release or renewed verification,
branch-changing rulings to the counted correction path, and a blocked ruling
to its saved operator question. Only the operator overrides a ruling.
Continue immediately to the step named by that outcome.

## Step 20 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 7 with the role `release` for
the developer's agent (template `templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 8 (it reports
`already-provisioned`), dispatch through Step 10 so the context is cleared and
the brief is fresh, and wait on the report in Step 11. A source-changing
release finding returns to Step 12 for the next counted developer assignment.
The worker merges after all gates pass. Proceed immediately to Step 21 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 21 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`. Proceed immediately to Step 22.

## Step 22 — Log the Round

Finalize the task ledger with the round outcome and remaining obligations.
Mark the task completed only after its acceptance criteria and required
release and cleanup obligations are verified. Preserve the ledger for resume
and standup. Preserve retrospective notes and link them from the ledger. Save
current progress through the attention owner and stow the lead's handoff under
the working-memory reference. Reconcile supervision before ending the turn.
Report outstanding attention first, followed by the outcome and saved paths.
Finish here.

For the daily standup, use
`Skill(skill: "herdr-standup")`.
