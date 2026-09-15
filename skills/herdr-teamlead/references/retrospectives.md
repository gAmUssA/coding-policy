# Team Retrospectives

The lead owns the retrospective. It synthesizes saved evidence; it does not
dispatch a retrospective role or spend a correction attempt. A routine standup,
usage measurement, status table, or dispatch dump is source material, not the
completed retrospective.

## When to run one

Check on active resume, before planning and dispatch, and between report waits.
The utility owns the elapsed-time calculation and daily interval. Complete a
retrospective when the active cadence is due. Existing work with no usable
retrospective history is due immediately. A new empty team's first active work
establishes its baseline. Do not install a scheduler or wake an inactive team.

Independently, complete one before clearing or relaunching a worker with outgoing
work, or changing its role, model, or effort. This includes release and judge
handoffs. A proven first-ever worker with no outgoing work is exempt from this
transition requirement; missing history or unknown session evidence does not
prove that exemption. Ordinary readiness, recovery, and acceptance gates still
apply after a retrospective.

A retained specialist follow-up remains subject to the daily cadence. Use
`context: "retain"` in its check request; the continuation owner verifies the
unchanged engagement under `references/dispatch-recovery.md`. A changed specialty
or engagement uses the normal fresh assignment and transition path.

One retrospective can cover the daily cadence and a batch of planned transitions.
Bind each worker independently to its outgoing assignment, native session and
process evidence, source report bytes, and proposed transition. Completing the
first transition does not invalidate an unchanged sibling's coverage. An identical
retry can reuse its coverage. New outgoing work, changed evidence or session, or a
different proposed seat, model, effort, or context action requires fresh coverage.
A recent daily note cannot cover work or transitions it never reviewed.

## Gather evidence without interrupting workers

Read the task ledger, assignment history, reports in full, VCS artifacts, review
findings, test and publish results, and measured headroom where available. Read
the previous retrospective and its open actions. Workers' normal reports include
handoff observations; use them without sending another prompt or clearing context.
Read-only status and process inspection may establish transition evidence.
Include saved specialist requirements, lead contribution assessments, and the
decisions that used or declined their output. An unused profile has no missing
report; distinguish unneeded expertise from expertise the task lacked.

Record participants whose saved observations were considered and workers whose
input is unavailable, with reasons. A busy or blocked worker receives no
retrospective prompt, recovery key, or context clear. Daily synthesis can proceed
with unavailable participation recorded. Missing reports, costs, usage, or outcome
proof remain `unknown`; they do not become inferred measurements or passes.
If a known review file is no longer readable, record that reason explicitly in
the request and synthesize from the remaining ledger or saved notes. The utility
preserves its archived receipt metadata and records unavailable current bytes;
restoring the file changes the evidence and requires refreshed transition coverage.

## Write the retrospective

Use a title and five nonempty H2 sections named `Outcomes`, `Quality`,
`Coordination`, `Seats and models`, and `Improvements`. The utility adds the saved
note's schema metadata; the lead supplies the synthesis under these headings.

Address these five questions with evidence and the lead's reasoning:

1. **Outcomes:** What did the team intend to achieve, what is verified, and what
   remains unresolved? Distinguish worker claims from accepted work and completed
   tasks. Cite task IDs, reports, full SHAs, and gate/run results where relevant.
2. **Quality:** Which review or test findings prevented defects? Which defects or
   misunderstood requirements escaped earlier checks? Separate observed facts
   from plausible explanations.
3. **Coordination:** Where did unclear briefs, repeated work, waiting, context loss,
   or recovery consume time? What did the outgoing workers learn that a fresh
   worker needs to know?
4. **Seats and models:** How well did the assignments fit the work? Use observed
   results, measured headroom, and available cost evidence; record absent data
   explicitly. A model name alone does not establish capacity or effectiveness.
   Which specialist changed a decision or prevented rework? Who arrived too late,
   and which consultation consumed time without a useful result? Evaluate whether
   a retained session or durable handoff preserved the knowledge the next worker
   needed. Separate measured costs from impressions.
5. **Improvements:** Which small set of changes should the team try next? Give
   each action an owner, next checkpoint, and observable success criterion. Revisit
   earlier actions as completed with evidence, still open, or superseded with a
   reason. If no change is warranted, explain that conclusion from the evidence.

Do not pad a quiet interval with invented incidents or speculative failures.
Record what the evidence supports, even when the result is a short no-change
conclusion. Keep facts, explanations, and proposed actions distinct. A populated
template alone is insufficient: the lead checks that the notes contain useful
lessons or an evidence-backed no-change conclusion before recording them.

Retrospective actions do not modify policy automatically, expand the task, grant
more correction attempts, waive a release gate, or authorize an uncertain resend.
Carry an in-scope improvement into already authorized work. Record proposals that
need separate work with their owner and next decision point.

After saving the note, curate lessons that should inform later tasks through
`skills/herdr-teamlead/references/working-memory.md`. Link each lesson to this
note and retain its applicability and revalidation conditions. Route task-specific
facts to the task ledger and project-wide changes through the normal authorized
work flow. Save user decisions, review requests, and promised follow-ups through
`skills/herdr-teamlead/references/attention.md`. These records supplement the
completed note; they never rewrite it or silently change policy.
Use these findings to refine future capability declarations, briefing questions
and consultation timing. Validate the relevant skills, tools and observed work
before updating staffing assumptions; retrospective recommendations create no
automatic model promotion or new standing seat.

## Persistence and retrieval

Use the same canonical `--state` path across the team's invocations. The utility
saves notes and the index in the adjacent `.retrospectives` directory documented
in `skills/herdr-teamlead/state-schema.md`. Record that directory in the lead's
handoff, and link relevant notes from the task ledger. Preserve it when removing
worktrees and task report staging. Do not edit a saved note or hand-write a receipt;
record a new retrospective when the analysis changes.

On a request for the last retrospective, its history, or notes for a task or date,
retrieve the stored records before making Herdr calls. Read the actual saved note
bytes and report the saved date, covered period/tasks, conclusions, action status
as recorded, and absolute path. Label any later assessment separately. Do not
generate a new retrospective, contact workers, or imply an old action is now done.
If no matching usable notes exist, say so. Preserve corrupt or unsupported files
and report their diagnostic; never fabricate a history or overwrite the index.

## Command workflow

Run the installed `skills/herdr-teamlead/teamlead.sh` with an explicit `bash`
interpreter, using the plugin root resolved in the skill. Every command accepts
the team's same `--state <state-file>`. The command's help owns optional flags and
the exact output envelope; the state schema documents persisted fields.

1. Prepare a JSON request with `transitions`, an array of planned worker changes.
   Each entry names `agent`, `role`, `model`, `effort`, `context` (`clear`, `retain`,
   or `start`), `task`, `brief`, `common`, and outgoing `report`. Use absolute
   paths; unknown optional values are null. `unavailable` names a reason when
   worker input is missing, and `pane` identifies a planned start. Use an empty
   transition list for a daily check without planned changes.
2. Run `retro-check --record <request.json>` and save its JSON receipt. It checks
   cadence and proposed coverage using read-only live evidence. It emits `due`,
   `missing_coverage`, the source request, and coverage receipts. A successful
   command is a completed check, not a completed retrospective. Follow its
   diagnostic on non-zero; never replace missing proof with a hand-written result.
3. If due, coverage is missing, or the operator explicitly requested a new
   retrospective, gather evidence and write the substantive Markdown described
   above. Otherwise proceed silently to the calling checkpoint. Read the previous
   saved actions before writing the new note.
4. Prepare recording metadata with `id`, absolute draft `note`, timezone-qualified
   `period_start` and `period_end`, `triggers` (`daily`, `transition`, or both),
   covered `tasks`, `participants`, `unavailable` worker-to-reason map, absolute
   evidence `sources`, `completed: true`, and absolute saved `check` receipt.
   End the covered period at or after the receipt's `checked_at`, no later than
   completion. Include both outgoing and proposed task IDs from its coverage.
   Account for every checked worker as participating or unavailable, with no
   overlap. Include every applicable trigger: `daily` when cadence is due and
   `transition` when any checked worker requires transition coverage.
   Daily notes cite a ledger, previous note, or other evidence source. An empty
   source list is usable only when transition coverage supplies concrete evidence.
   Completion asserts the lead has reviewed the substance; the boolean alone
   never establishes it.
5. Run `retro-record --record <metadata.json>`. The utility revalidates evidence,
   preserves the completed note, and records its digest and coverage. Inspect the
   returned saved path and identity, then reference them in the task ledger. A
   failure leaves the cadence or transition unsatisfied; reconcile its diagnostic
   before proceeding. An identical record retry is idempotent.
6. Resume the checkpoint that requested the retrospective. A changed plan or
   source requires a new check and coverage before the affected transition.
   Recording notes alone never launches, clears, or dispatches workers.

`retro-list [--task <task-id>] [--since <ISO-time>]` lists saved records.
`retro-show [--id <retro-id>] [--task <task-id>]` returns the stored note, defaulting
to latest. Both operate offline and perform no dispatch-state migration or writes.
Use these commands for readback even with `HERDR_ENV` unset or no usable config.
An injected `--now <ISO-time>` on checks and recording supports reproducible timing;
normal operation uses the actual current time.
