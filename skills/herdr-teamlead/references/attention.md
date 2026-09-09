# Persistent user attention

The lead owns a durable queue of user-facing obligations. Persist an obligation immediately when the lead opens a question, requests a decision or review, identifies a user-relevant blocker or failure, or promises an update or follow-up. Save it before further housekeeping or a context handoff. Record actual answers, review outcomes, and completed deliveries immediately after receiving or making them. An unrelated user message never changes a queue entry.

Before a catch-up, resumption, standup, or lead handoff, read this queue. Present the most consequential actionable items ahead of reasoning, runtime status, and housekeeping. The lead sets priority from impact and urgency; the utility does not infer importance from prose. Present concise context, consequences, available choices, and a recommendation where useful. Showing an item does not resolve it. A local preview is not evidence that the user saw it.

## Commands and files

Run through the installed `skills/herdr-teamlead/teamlead.sh` with an explicit `bash` interpreter and the plugin root resolved in the skill. Each command returns JSON; report a non-zero command's diagnostic before continuing. The `markdown` and `attention_markdown` fields are ready to present. Commands accept the selected `--state` path and operate without config, Herdr, worker messages, or a readable dispatch-state document.

| Command | Input | Output |
| --- | --- | --- |
| `attention-record --record entry.json [--now ISO]` | New obligation | Saved event and `replayed` |
| `attention-update --record event.json [--now ISO]` | Explicit lifecycle transition | Saved event and `replayed` |
| `attention-progress --record progress.json [--now ISO]` | Lead-assessed progress fact with ledger source | Saved event and `replayed` |
| `attention-list [--task ID] [--limit N] [--offset N] [--since ISO] [--include-closed] [--now ISO]` | Offline view | Same structured view as `catch-up` |
| `catch-up [--task ID] [--limit N] [--offset N] [--since ISO] [--include-closed] [--now ISO]` | Offline view | Actionable, deferred, progress, optional closed pages; source pointers; rendered Markdown |
| `attention-show --id ID [--now ISO]` | Obligation ID | Current record and its complete lifecycle history |

The owner file is `<Path(selected --state).expanduser().resolve()>.attention.json`. Its adjacent `.lock` is a live OS transaction lock. The owner uses `state_lock` and `save_state` for serialized atomic replacement. Preserve both the attention history and task ledgers outside task worktrees during cleanup. Canonical aliases of the selected state share one queue. Readers never create locks, initialize files, migrate data, update presentation markers, or contact workers.

The schema-1 document contains exactly `schema_version`, canonical `state_path`, and append-only `events`. Each event contains `schema_version: 1`, `event_id`, UTC `at`, `action: record|update|progress`, and its validated `data`. Materialized obligation, progress, source, and resolution-evidence records carry `schema_version: 1`. Only `herdr-teamlead` writes; standup and saved-note/report readers read only. Read missing storage as no prior queue. Refuse malformed, unknown, older, newer, mismatched-state, or inconsistent history without replacing its bytes. Version 1 has no predecessor migration. Future shape changes require a version bump and an owner migration before writes.

## Record an obligation

Example `entry.json`:

```json
{
  "id": "repo-42-api-choice",
  "kind": "decision",
  "task": "owner/repo#42",
  "title": "Choose the supported API boundary",
  "context": "The review proposes supporting older clients beyond the agreed API.",
  "consequence": "That extra compatibility work is awaiting your decision.",
  "resolution_condition": "Record the user's choice about supporting older clients.",
  "priority": 80,
  "options": ["Keep the agreed API", "Expand support to older clients"],
  "recommendation": "Keep the agreed API for this release.",
  "sources": [
    {"schema_version": 1, "kind": "user_message", "ref": "conversation/task-42/message-8"},
    {"schema_version": 1, "kind": "task_ledger", "ref": "/reports/task-42/TASK-LEDGER.md"}
  ]
}
```

Required fields are `id`, `kind`, `title`, `context`, `consequence`, `resolution_condition`, and nonempty `sources`. Optional fields default to `task: null`, `priority: 50`, `options: []`, and `recommendation: null`. Kinds are `question`, `decision`, `review`, `blocker`, `failure`, `followup`, and `update`. Use stable obligation IDs tied to the task and the actual question or promise; do not create a new ID each time it is shown. IDs use letters, digits, dots, underscores, colons and hyphens, begin with a letter or digit, and contain at most 110 characters. The internal creation event ID is `record:<id>`.

Priority is a lead-assigned integer from 0 to 100; 100 is most consequential. `options` contains at most ten plain-text choices. `sources` contains 1–30 versioned `{schema_version: 1, kind, ref}` records; kinds are `user_message`, `task_ledger`, `retrospective`, `artifact`, and `other`. References may be absolute file paths, URLs, or durable conversation/message identifiers. Preserve enough quoted or summarized answer context in the lifecycle evidence to survive a lost conversation. Avoid secrets and unnecessary personal data. The utility stores references without dereferencing them.

## Lifecycle and evidence

Read `attention-show` before changing an existing entry. Its `revision` starts at 1. Supply the current revision in every update; stale revisions fail without changing history. Each intended transition receives its own stable `event_id`, up to 128 characters using the same identifier alphabet. An identical retry returns its original event with `replayed: true`; conflicting reuse fails. This holds across restarts and a write whose response was lost. Use a fresh event ID only for an intentional new event, not to bypass an unreconciled retry.

Every update has `event_id`, `id`, `expected_revision`, `action`, `reason`, and `evidence`. Evidence contains `{schema_version: 1, kind, ref, summary}`. The lead checks that the evidence actually addresses this obligation and satisfies its recorded resolution condition; a field named `user_answer` alone establishes no authorization. Never copy a generic acknowledgement or unrelated message into answer evidence. Retain the meaningful answer or outcome in `summary` and its durable origin in `ref`.

| Action | Additional fields | Effect |
| --- | --- | --- |
| `present` | Evidence kind `delivery` | Records actual user-facing presentation; status stays unchanged |
| `resolve` | Matching evidence below | Closes this obligation; original question and every earlier event remain |
| `defer` | Future `until` timestamp and evidence | Keeps the obligation pending until the recorded checkpoint |
| `reopen` | Evidence of changed circumstances or the user's revised decision | Reopens a deferred or closed entry; prior resolution remains in history |
| `supersede` | `replacement` ID and evidence | Points to a distinct existing open/deferred replacement; old history remains |
| `amend` | Nonempty `changes` and evidence | Revises context, title, consequence, resolution condition, sources, options, recommendation, or priority |

Questions and decisions resolve only with `user_answer`. Reviews require `review_outcome`. Blockers accept `acknowledgement`, `user_answer`, or `verified_outcome` according to their recorded resolution condition. If independent evidence verifies that the blocker recovered, close the user-facing obligation with that evidence; do not require an unnecessary acknowledgement. A separate pending decision still needs the actual user answer. Failures accept `acknowledgement`, `user_answer`, `delivery`, or `verified_outcome`, according to their recorded resolution condition. A purely informational failure can close with explicit evidence that the user was notified; do not invent an acknowledgement chore. A failure whose condition is recovery can close with a verified outcome linked to its actual validation evidence. Keep a failure open if the user still owes a decision; delivery alone does not satisfy that condition. Follow-ups and updates require `delivery`. `present` never closes an entry automatically, including an informational failure; record a separate explicit `resolve` after checking that the evidence satisfies the condition. An entry's resolution closes its user-facing obligation only; it never resolves the underlying technical blocker, accepts a worker report, grants implementation authority, or completes a task. `source` evidence is accepted for supported non-resolution transitions, such as a lead's recorded rationale for deferral or an amended priority.

Example answer event:

```json
{
  "event_id": "repo-42-api-answer-1",
  "id": "repo-42-api-choice",
  "expected_revision": 1,
  "action": "resolve",
  "reason": "The user's answer addresses this API boundary decision.",
  "evidence": {
    "schema_version": 1,
    "kind": "user_answer",
    "ref": "conversation/task-42/message-11",
    "summary": "Keep the agreed API for this release; older-client support remains out of scope."
  }
}
```

Deferred entries resurface automatically at `until` using the caller's `--now` or current checkpoint. Readback reports `effective_status: open` and `resurfaced: true`; it does not rewrite the saved `status: deferred`. Showing a resurfaced entry still leaves it pending. No running scheduler or inactive-session wake is implied by the attention queue.

## Catch-up and progress

Catch-up ordering and page bounds are owned by `skills/herdr-teamlead/teamlead/attention_view.py`, `catch_up`. It includes unpresented and previously presented entries. `--since` filters only progress and closed history; old unanswered questions remain visible. `--task` explicitly limits the view to that task. Every section reports `total`, `offset`, `limit`, `returned`, `omitted`, `next_offset`, and `items`. Follow pagination before claiming to have shown all pending items. Markdown warns when actionable obligations remain outside the current page. `attention_markdown` contains only actionable obligations and pagination warnings, or an empty string when none exist; standup places it before its housekeeping table. Rendered items lead with the title, context, consequences, choices, recommendation, and needed action. Use user-readable wording in the resolution condition. Internal IDs, numeric priorities, revisions, presentation metadata, and the full source inventory remain in structured JSON. Markdown shows useful source links per item and a short still-open or resurfaced cue when applicable.

The view includes future deferrals, explicitly recorded progress, optional closed entries, source references for displayed records, and the canonical retrospective-index path. It does not scan the filesystem or parse report prose to infer completion. Read the task ledger and saved retrospectives when their content is needed.

A progress input contains exactly `id`, `task`, `summary`, `assessment`, and `sources`. Assessment is `verified`, `reported`, or `unknown`; at least one source must be the associated `task_ledger`. Use `reported` for unaccepted worker claims. The utility records its UTC event time. The catch-up shows the timestamp with **Verified when recorded**, **Reported; acceptance unverified**, or **Acceptance unknown**. Even `verified` means verified when recorded; revalidate the actual task ledger and source evidence before claiming current acceptance. This cached summary never replaces the task ledger or treats Herdr lifecycle labels as acceptance. Give a changed fact a new progress ID; preserve the earlier claim and correction.

The utility cannot infer which human words answer which question, whether an artifact meets acceptance criteria, or whether a changed scope is authorized. Those are lead judgments recorded with evidence. Deterministic responsibilities are storage, schema validation, lifecycle constraints, idempotency, ordering, deferral deadlines, bounded views, and preservation across resets.
