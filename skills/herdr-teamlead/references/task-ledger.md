# Task Ledger

The lead records its judgments in one persistent `TASK-LEDGER.md` for the task.
Its location, schema, ownership, and migration contract are in
`skills/herdr-teamlead/state-schema.md`, Task Ledger. The JSON utility ledger
continues to record what was dispatched and which recovery decisions were
authorized. An `applied` row there says nothing about whether the work passed.

## Start or Resume

At Step 3, establish the stable task identity, original base, and absolute ledger
path. Save that path beside the task's authorization context and include it in
every lead handoff. Create the document on a first run, or read the existing
document in full before planning more work. Do not create a new ledger per fix.

Read the referenced utility state through its owner commands. Correlate each
assignment with its dispatch ID, worker, role, and fresh report path. Never edit
`state.json` by hand to make it agree with a Markdown decision.

On resume, inspect evidence relevant to the next action: the actual report and
its content, branch tip/diff, review and test results, release state, and live
pane/native evidence for work that may still be running. Stale Herdr `working`
does not undo accepted work; `idle` or `done`, however often observed, does not
accept work. A conflicting observation requires reconciliation, not a resend.

## Record Decisions as They Happen

Append an event after each dispatch outcome, wait outcome, and evidence-based
assessment. Record missing reports and unknown send outcomes before continuing
another worker. Append blocker, judge, release, cleanup, and handoff decisions
when they happen; Step 22 finalizes the log rather than creating it from memory.

Use these separate vocabularies:

| Subject | Decision | Meaning |
| --- | --- | --- |
| assignment | `pending` | Dispatch is planned or confirmed; no report has been assessed |
| assignment | `reported` | Delivery was confirmed; the lead has not yet accepted the work |
| assignment | `accepted` | The lead read the report and verified that the assignment's acceptance criteria hold |
| assignment | `needs_work` | Evidence shows unmet criteria or invalidates a prior acceptance |
| assignment | `blocked` | A specific unresolved dependency or decision prevents the assignment from proceeding |
| assignment | `unavailable` | A report is missing or unavailable under the wait/recovery contract |
| assignment | `unknown` | Dispatch or outcome evidence is insufficient; reconcile before retrying |
| task | `in_progress` | Required task work remains |
| task | `waiting_for_operator` | A named required operator decision remains outstanding |
| task | `ready_for_release` | Step 12's current-tip verification gate holds; release remains outstanding |
| task | `completed` | All task acceptance criteria and required release/cleanup obligations are verified |

Record Herdr's label under `observed`, with its source. Record worker claims as
claims there too. Neither is the lead's `decision`. `found: true` confirms report
delivery only. A report containing `## BLOCKED` is not successful task completion.
A reviewer assignment can be accepted as a completed review while its blocking
findings keep the task `in_progress`.

Bind every acceptance to the specific report content and artifact inspected.
Record the full SHA for repository work and link test/review evidence for that
SHA. Preserve evidence files for resume. A new push makes older verification
insufficient for release: append that invalidation, put the task back in progress,
and obtain Step 12's current-tip verification. Keep the old report and decision.

Verify release claims against the live PR, merge, and any required publication
checks under `rules/ci-safety.md`. An accepted release report is not a substitute
for those checks. Record remaining obligations explicitly before claiming the
task complete. The ledger never bypasses dispatch readiness or permits input
into a working or blocked worker.

## Blank Document and Event

Fill this as a reasoning document; placeholder values are not evidence.
Append one uniquely named event section per decision. For task events use
`not_applicable` for the assignment fields. Use `unknown` for unavailable evidence.

```markdown
---
schema_version: 1
task: <stable task identifier>
base_revision: <full original SHA>
dispatch_state: <absolute utility state.json path>
---

# Task Ledger

## <unique event id>

- schema_version: 1
- id: <same unique event id>
- at: <timezone-qualified observation time>
- subject: <assignment or task>
- dispatch_id: <actual dispatch id or not_applicable>
- worker: <worker name or not_applicable>
- role: <role or not_applicable>
- report: <absolute path or unknown>
- observed: <source and its actual observation>
- decision: <lead assessment from the table>
- head_revision: <full inspected SHA, unknown, or not_applicable>
- evidence: <paths/content/digests, refs, gate URLs/results, or unknown>
- assessment: <reason, remaining criteria, next action>
```
