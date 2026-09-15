---
name: herdr-teamlead
description: >
  Run Herdr rounds with on-demand specialists, qualified tiers, bounded briefs,
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

Follow `rules/agent-team-operation.md` for round constraints.

Each command resolves `CP` to the local or home plugin. Repeat its resolver in
every call. Prose `skills/...` paths are relative to that root.

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
skills/herdr-teamlead/references/specialists.md
skills/herdr-teamlead/state-schema.md
```

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

Record staffing gaps under `skills/herdr-teamlead/references/round-setup.md`. Leave unused specialist
profiles unlaunched. Never duplicate targets or fold verification onto a contributor.
Start workers in YOLO mode under `skills/herdr-teamlead/references/model-tiers.md`; preserve it on
relaunch. Verify live permission flags before dispatch, including existing workers.

## Step 3 — Verify Authority for the Repo

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/verify-authority.sh" <owner/repo>
```

Record the emitted namespace ownership evidence using Step 3 of
`skills/herdr-teamlead/references/round-setup.md`. For a non-owned repo, reuse explicit per-action
operator permission; absent permission, remain read-only or finish here.
On non-zero, report the diagnostic and finish here.

Record task authorization and permitted actions under the round-setup reference.
Create or resume the stable ledger under `skills/herdr-teamlead/references/task-ledger.md`; record its
absolute path before dispatch. Apply the round-setup accepted-behavior, resume
and supervision binding requirements.
Proceed immediately to Step 4.

## Step 4 — Measure Headroom

Run `skills/herdr-teamlead/references/retrospectives.md` on resume, before planning, or for an explicit
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

Choose the responsibilities needed next under `skills/herdr-teamlead/references/specialists.md`.
Supply its requirements file for specialized work. Keep the developer reserved
through early fixes; schedule consultation and verification as the task needs them.

The composition triggers decide part of that roster. Classify this round
against the repo's declaration first. For a pre-implementation round, pass
`--planned` naming the surfaces the work will touch. A round with work already
written classifies that work:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" detect-triggers \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--planned <planned.json>] [--decisions <decisions.json>]
```

Exit 0 means every fired trigger is staffed or answered. On exit 1, read the
stderr object: an absent declaration is written first (`skills/herdr-teamlead/references/specialists.md`),
and an `unaddressed_trigger` is staffed in the roles below or answered by a
recorded decision with its reason. Re-run the command with the updated
declaration, roles, requirements and decisions after every such change, and
plan only once it exits 0.

A round that will split its review surface validates the partition first:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" validate-partition \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --partition <partition.json>
```

Exit 1 names every unowned path, every overlap and every slice owning nothing,
in one run. Fix the partition and re-run; plan only once it exits 0. The document's format, the ownership
payload and the seating it produces:

```text
skills/herdr-teamlead/references/review-partition.md
```

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--exclude <role>=<agent>[,<agent>...]]... [--judge-mode adjudication|diagnosis] \
  [--round <role>=<round-type>] [--round-context <evidence.json>] \
  --task <task-id> [--fix-round <N>] [--correction-plan <id> --work <work.json>]
```

Emits the role plan without worker contact. A judge seat declares its mode:
`adjudication` rules on a contested verdict, `diagnosis` on the investigator's
assessment at an exhausted allowance. Pass the same `--judge-mode` to `apply`.
On exit 1, resolve the diagnostic before continuing. Apply the Step 5 constraints in `skills/herdr-teamlead/references/round-setup.md`:
exclude contributors from verification, reserve the developer through early fixes,
preserve task identity and fix count, and reuse recorded correction bounds.
Tier and qualification contracts:

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
`skills/herdr-teamlead/references/round-setup.md`. Success prints the absolute review-package path;
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

Follow `skills/herdr-teamlead/references/round-setup.md` Step 7 for shared and role-specific values,
authority, review evidence and brief completeness.
Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

Prune first, every round:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/prune-worktrees.sh" <shared-checkout>
```

Emits the worktrees and branches removed, each kept one with its reason, and
`failed`; exit 2 lists every check or removal git refused. Exit 1 decided
nothing: fix its diagnostic and re-run before provisioning. Report every kept
`dirty`, `unmerged`, `locked` and `detached` entry to the operator; never
remove them by hand.

Then run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits path, branch, base, and `created|attached|already-provisioned`. On any
non-zero exit, fix the diagnostic and retry. Never dispatch a missing
worktree. Read-only consultations need none. Clean up after merge per
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
  --brief <role>=<path> [--brief <role>=<path>]... \
  --report <role>=<report> [--report <role>=<report>]... \
  --common <path-to-COMMON.md> --task <task-id> \
  [--fix-round <N>] [--retain-context | --retain-specialist | --no-clear] \
  [--correction-plan <id> --work <work.json>] [--dispatch-id <stable-id>]
```

Emits dispatch JSON under `state-schema.md`. Supply each role's fresh absolute
report path from its brief. Apply enrolls before input; unknown sends remain
observation obligations. Apply refuses while an open decision or blocker on the
task is unanswered; see the attention reference's Dispatch gate.
Classify every brief against Step 3's authorization before sending it.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the recovery reference's Dispatch context requirements before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone. Warm consultations use the recovery reference's
`--retain-specialist` path. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

Follow the Dispatch Results contract in `skills/herdr-teamlead/references/round-flow.md` for busy,
uncertain, failed, and dry-run outcomes. Preserve all already enrolled work.

Dispatch references:

```text
skills/herdr-teamlead/references/dispatch-recovery.md
skills/herdr-teamlead/references/model-tiers.md
```

Proceed to Step 11.

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
bash "$CP/skills/herdr-teamlead/wait-report.sh" --once \
  [--worktree <worker-checkout>] [--base <dispatch-base>] \
  [--since <dispatch-sent-at>] <agent-name> <report-path>
```

The checkpoint emits delivery JSON; exit 2 emits only stderr. Exit 0 confirms
delivery, 1 remains pending, 3 confirms blocked, 4 lacks confirmed delivery, and
5 proves terminal refusal; record it with `record-refusal`. Read delivered
reports in full. Pass this dispatch's recorded send time as `--since`, its
recorded base as `--base`, and the worker's checkout as `--worktree`. An exit 1
then carries either `reason: checkpoint_pending` or a `stall` object; act on a
stall under `rules/agent-team-operation.md` Stalled Workers and record the
obligation through `skills/herdr-teamlead/references/attention.md`. Preserve the blocked/refusal and native-recovery paths in the
following references; never re-dispatch over uncertainty or resend a refused
brief to its provider.

```text
skills/herdr-teamlead/references/supervision.md
skills/herdr-teamlead/references/dispatch-recovery.md
```

Save a consultation's successful delivery receipt and record `assess-specialist`
under `skills/herdr-teamlead/references/specialists.md` before retiring its enrollment.

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
Assess correction scope and bug evidence under `skills/herdr-teamlead/references/assignment-reasoning.md`.
Persist user-facing obligations under `skills/herdr-teamlead/references/attention.md` before presenting
them; record an actual answer or resolution separately from showing the item.

After accepting a consultation, return to Step 4 for the next needed
responsibility. For an investigation-only task, use the knowledge gate below.

For an investigation-only task, assess every assigned report against the requested
knowledge deliverable. Resolve blocking findings through the same bounded and
judge paths below. Once its criteria hold, present the findings and preserve open
user decisions; proceed to Step 21 if a task worktree needs cleanup, otherwise
Step 22. No implementation or release is inferred from the diagnostic result.

- **Any blocking finding** — apply the round-flow reference's Blocking Gate
  contract and `rules/agent-team-operation.md` Fix Loops. Return to Step 4 for
  an authorized correction or Step 13 for a required judge ruling. At an
  exhausted allowance, record the checkpoint through
  `skills/herdr-teamlead/references/dispatch-recovery.md`, consult the investigator under
  `skills/herdr-teamlead/references/specialists.md`, and take its assessed report to Step 13 for the
  diagnosis; no operator decision is awaited.
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

Optional. Modes, triggers and both report contracts are in
`skills/herdr-teamlead/references/round-flow.md` "The Judge" (a bot
disagreement inside Step 20 returns here first). No trigger — proceed to
Step 20.

For a dispute, compose from `skills/herdr-teamlead/templates/brief-judge.md` through Step 7: the
dispute, both positions with report paths, the governing rule, the tree.

For an exhausted allowance, compose from `skills/herdr-teamlead/templates/brief-judge-diagnosis.md`
through Step 7 under the role key `judge-diagnosis`, which writes
`brief-judge-diagnosis.md`: the assessed investigator report, the task, rounds
spent, remaining blocking work, the per-round history, the tree, and any prior
remedy with what it changed.
The pinned seat is still `judge`, so plan and dispatch that role and pass this
file as its brief: `--brief judge=<outdir>/brief-judge-diagnosis.md`.

Skip Step 8 for the read-only judge. Proceed immediately to Step 14.

## Step 14 — Re-measure the Shared Window

Re-run Step 4's `measure` under its outcome contract. Resolve any unreadable
judge window before planning. Pass the fresh snapshot to Step 15; never reuse
the earlier reading as affordability proof. Proceed immediately to Step 15.

## Step 15 — Plan the Judge Seat

Plan the pinned judge against Step 14's fresh snapshot:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles judge --judge-mode <adjudication|diagnosis> \
  --snapshot <step-14-measure-output> --task <task-id>
```

`adjudication` for a dispute, `diagnosis` for an exhausted allowance — the same
choice Step 13 made when it composed the brief. Use the recorded mode in Steps
16 and 17. An undeclared mode is refused.

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

Starts the pinned judge and verifies launch argv, on the mode Step 15 recorded
in the plan. The header owns the contract.

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
  --common <path-to-COMMON.md> --judge-mode <adjudication|diagnosis> \
  --task <task-id> [--no-clear]
```

Pass the same `--judge-mode` Step 15 planned. Step 10's outcomes govern. Use
`--no-clear` only for the worker just started in
Step 16; an existing judge receives the default cleared relaunch with retrospective
coverage. Apply verifies the live tier before input. Proceed immediately to Step 18.

## Step 18 — Wait for the Ruling

Run Step 11's fleet observation loop, including the judge named by Step 15.
Proceed immediately to Step 19 once its report lands; keep other enrollments
under observation.

## Step 19 — Act on the Ruling or Remedy

Apply the Ruling Outcomes contract in `skills/herdr-teamlead/references/round-flow.md`. Investigation
rulings return to Step 12's knowledge gate. Implementation rulings route
unchanged-branch rulings to verified release or renewed verification,
branch-changing rulings to the counted correction path, and a blocked ruling
to its saved operator question. Only the operator overrides a ruling.
Continue immediately to the step named by that outcome.

## Step 20 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 7 with the role `release` for
the developer's agent (template `skills/herdr-teamlead/templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 8 (it reports
`already-provisioned`), dispatch through Step 10 so the context is cleared and
the brief is fresh, and wait on the report in Step 11. A source-changing
release finding returns to Step 12 for the next counted developer assignment.
The worker merges after all gates pass. Proceed immediately to Step 21 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 21 — Clean Up the Worktree

Fast-forward the shared checkout, remove the worktree, and delete the branch
per `rules/agent-worktree-isolation.md`, then run Step 8's prune script again
for the round's other worktrees. Proceed immediately to Step 22.

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
