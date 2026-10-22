# Round Dispatch Reference

Execution detail for Steps 8–10 of `skills/herdr-teamlead/SKILL.md`. The skill
keeps the step order and the command blocks; read the matching section here
before running each step.

## Step 8 — Dispatch the Briefs

Complete the retrospective reference's cadence and transition checks before live
dispatch. Apply rechecks coverage before worker input. Dry runs prove no coverage.

Run the `apply` command in the skill's Step 8. It emits dispatch JSON under
`skills/herdr-teamlead/state-schema.md`. Supply each role's fresh absolute
report path from its brief. Apply enrolls before input; unknown sends remain
observation obligations. Apply refuses while an open decision or blocker on the
task is unanswered; see the Dispatch gate in
`skills/herdr-teamlead/references/attention.md`.
Classify every brief against Step 2's authorization before sending it.
Append the dispatch outcome to the task ledger; `applied` proves dispatch only.

Apply the Dispatch context requirements in
`skills/herdr-teamlead/references/dispatch-recovery.md` before sending.
Preserve task identity and cumulative fix count. Retained fixes dispatch
developer alone. Warm consultations use that reference's
`--retain-specialist` path. Reconcile unknown outcomes
before retrying. Reuse existing correction authorization within its bounds.

Follow the Dispatch Results contract in `skills/herdr-teamlead/references/round-flow.md` for busy,
uncertain, failed, and dry-run outcomes. Preserve all already enrolled work.
Tier contracts are in `skills/herdr-teamlead/references/model-tiers.md`.
Proceed to Step 9.

## Step 9 — Observe the Fleet

Run the skill's Step 9 `supervision-watch` for all enrolled assignments. Retain
and await its real execution handle. The JSON result gives `reason`, `through`,
and durable `events`; a quiet deadline completes only that checkpoint.
For each event or pending recheck, verify report delivery with the skill's
Step 9 `wait-report.sh --once` command.

The checkpoint emits delivery JSON; exit 2 emits only stderr. Exit 0 confirms
delivery, 1 remains pending, 3 confirms blocked, 4 lacks confirmed delivery, and
5 proves terminal refusal; record it with `record-refusal`. Read delivered
reports in full. Pass this dispatch's recorded send time as `--since`, its
recorded base as `--base`, and the worker's checkout as `--worktree`. An exit 1
then carries either `reason: checkpoint_pending` or a `stall` object; act on a
stall under `skills/herdr-teamlead/references/team-operation.md` Stalled Workers and record the
obligation through `skills/herdr-teamlead/references/attention.md`. Preserve the
blocked/refusal and native-recovery paths in
`skills/herdr-teamlead/references/supervision.md` and
`skills/herdr-teamlead/references/dispatch-recovery.md`; never re-dispatch over
uncertainty or resend a refused brief to its provider.

Save a consultation's successful delivery receipt and record `assess-specialist`
under `skills/herdr-teamlead/references/specialists.md` before retiring its enrollment.

Record each outcome in the task ledger and user-facing obligations in the
attention queue. Acknowledge only handled event IDs through the saved snapshot;
schedule pending rechecks. Resolve enrollment only after recording its assessed
outcome, separately from assignment acceptance and task completion. Complete due
retrospectives between checkpoints without interrupting workers. Resume the fleet
watch while any observation obligation remains; one blocked worker never hides
another worker's report. Proceed to Step 10 when the required reports are delivered
or their unavailability and recovery are recorded.

## Step 10 — Gate the Round

Read every report file in full, including a report whose worker exited cleanly.
A `## BLOCKED` section can sit under a report that otherwise reads as finished.
Classify each finding blocking or advisory per `rules/review-severity.md`.
Record assignment acceptance or outstanding work in the task ledger against
the inspected report and artifact evidence. Record the task's gate decision
separately; a worker finishing its brief never completes the whole task.
Assess correction scope and bug evidence under `skills/herdr-teamlead/references/assignment-reasoning.md`.
Persist user-facing obligations under `skills/herdr-teamlead/references/attention.md` before presenting
them; record an actual answer or resolution separately from showing the item.

After accepting a consultation, return to Step 2 for the next needed
responsibility. For an investigation-only task, use the knowledge gate below.

For an investigation-only task, assess every assigned report against the requested
knowledge deliverable. Resolve blocking findings through the same bounded and
judge paths below. Once its criteria hold, present the findings and preserve open
user decisions; proceed to Step 19 if a task worktree needs cleanup, otherwise
Step 20. No implementation or release is inferred from the diagnostic result.

- **Any blocking finding** — apply the round-flow reference's Blocking Gate
  contract and `skills/herdr-teamlead/references/team-operation.md` Fix Loops. Return to Step 2 for
  an authorized correction or Step 11 for a required judge ruling. At an
  exhausted allowance, record the checkpoint through
  `skills/herdr-teamlead/references/dispatch-recovery.md`, consult the investigator under
  `skills/herdr-teamlead/references/specialists.md`, and take its assessed report to Step 11 for the
  diagnosis; no operator decision is awaited.
- **Advisory findings only** — record them in the round log and fold them into
  the next round that is already happening. Never spend a round on a lone
  advisory.

Apply the Release Gate in `skills/herdr-teamlead/references/round-flow.md`;
obtain broad independent reviewer and tester passes against the current pushed
tip before release. With its release criteria met, proceed immediately to Step 11.
