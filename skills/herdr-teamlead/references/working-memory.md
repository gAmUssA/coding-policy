# Lead Working Memory

The lead curates lessons from evidence and preserves knowledge that would otherwise disappear with its conversation. Retrospective notes remain immutable. Task acceptance remains in the task ledger. Memory is a source of candidate lessons and handoff context; it cannot authorize actions or establish task completion.

Run the subcommands below through the installed
`skills/herdr-teamlead/teamlead.sh` with an explicit `bash` interpreter and the
plugin root resolved in the skill. Use the team's same `--state` path. Each
command emits JSON; report a non-zero command's diagnostic before continuing.

## Curate lessons

After synthesizing a retrospective, inspect the current lessons. Add a lesson only when it changes a future decision or brief. State the behavior, its relevance scope, the evidence supporting it, when the lead last verified that evidence, and when the lesson must be revalidated. Link the actual retrospective, report, review, or source document. Capture unevidenced hypotheses as unresolved stow knowledge with an explicit gap.

Before planning a round or composing a brief, use `memory-list` with the relevant project, task and role scopes. Read the source and verify the lesson against current conditions before applying it. Include only applicable lessons in the brief, with their source links. A `same_bytes` observation says the local file matches its saved receipt; it does not confirm the claim or renew verification. HTTPS evidence is never fetched by these offline commands. Expired, changed or unavailable evidence requires investigation before use.

Keep the active set concise. Merge overlapping lessons through a new revision, retaining the replaced record. Archive lessons that no longer affect active work with a concrete reason. A new active revision can reactivate an archived lesson after verification. Never delete historical revisions or rewrite the retrospective to make a lesson appear supported. These operations do not change policy, task authority, correction budgets or user preferences.

```text
memory-list --scope project:owner/repo --scope role:developer
memory-record --record /durable/team/lesson-input.json
memory-show --id test-command-1
```

Minimal lesson input:

```json
{
  "id": "test-command-1",
  "lesson_id": "test-command",
  "supersedes": null,
  "status": "active",
  "lesson": "Include the exact project test command in each bug-fix brief.",
  "scopes": ["project:owner/repo", "role:developer"],
  "sources": ["/durable/team/state.json.retrospectives/daily-1.md"],
  "last_verified_at": "2026-09-01T12:00:00Z",
  "expires_at": null,
  "revalidate_when": "The project test entrypoint changes.",
  "reason": "The retrospective traced repeated setup to the omitted command."
}
```

Each revision needs a new `id`; retain `lesson_id` and set `supersedes` to its current record's `id`. An archive uses the same shape with `status: "archived"` and an archive reason. Reverification is a new revision with its actual verification time and newly captured evidence receipts. Repeating an identical command returns its original record and receipts without refreshing them. Reusing an id for changed input fails visibly.

Scopes are exact labels chosen consistently by the lead; `*` applies globally. Multiple requested scopes select their union. `memory-list` returns the latest active revision of each matching lesson, including expired lessons labeled as expired. `--include-archived` includes current archived revisions. `memory-show --id <record-id>` returns that immutable record and its lesson's complete revision history.

## Preserve specialist knowledge

Use the same memory owner for specialist project knowledge. Add a consistent
specialty label, such as `specialty:ux-product`, alongside the applicable project
and task scopes. A worker proposes evidence-linked lessons in its report; the
lead checks and curates them. Keep actual user decisions, contributor identities
and assignment acceptance in the task ledger and owner state, with links from
lessons when useful.

Before a consultation, read the relevant project and specialty lessons and
revalidate their sources. Scope selection is a union, so inspect every returned
lesson's applicability before transferring it to another project. Supply the
selected knowledge through `KNOWLEDGE` in the consultation brief or
`SPECIALIST_CONTEXT` in a developer or verifier brief.

Before releasing a useful session, capture decisions, rejected alternatives,
unresolved evidence and lessons that would otherwise disappear. Link the outgoing
report, lead assessment and retrospective from the task handoff. A warm session
can support follow-up under `references/dispatch-recovery.md`; it is never the
only copy of valuable knowledge. Give a fresh worker the durable sources without
claiming that recalled context proves present competence or independence.

## Stow before replacing the lead

Before planned lead context compaction, restart, model change or replacement, sweep the conversation for knowledge that is still only in context. Persist accepted decisions, unanswered questions, promised updates, unresolved work, assumptions and useful lessons in their appropriate owner artifacts. Put user-facing obligations in the attention queue; reference their durable ids in the stow. Do not treat displayed questions as answered.

Record a stow containing:

- A substantive capture of the remaining context the next lead needs.
- Unresolved work, including where each item is now recorded or what the replacement must recover.
- Explicit gaps, including missing evidence and work the lead could not persist. Use an empty list only after checking for gaps.
- Ordered, absolute paths to the durable files the replacement must read, such as the task ledger, attention queue, active assignment state, relevant retrospective notes and task context. Use actual files, not directories or a vague instruction to inspect the workspace.

```text
memory-stow --record /durable/team/stow-input.json
memory-show
```

Minimal stow input:

```json
{
  "id": "lead-handoff-1",
  "capture": "The user asked to review the final artifact before publication. Attention item publish-review preserves that decision.",
  "unresolved_work": ["Finish the artifact, then surface attention item publish-review."],
  "gaps": [],
  "required_reads": ["/durable/team/TASK-LEDGER.md", "/durable/team/state.json.attention.json"]
}
```

The output supplies `memory_path`, the stow record and receipts for each required read. Preserve that exact `memory_path` in the lead handoff. Do not list the memory index as a source or required read: its path is included automatically, and writing the stow changes its bytes. The replacement first reads this index through `memory-show`, then the stow's required files in order, then the relevant lessons through `memory-list`. `memory-show` defaults to the latest stow; `--id` retrieves an earlier capture.

`reset_ready` is false if the stow has gaps or any required file has changed or become unavailable. A true value covers only the saved local capture and its unchanged required files. It does not prove that the lead captured every conversation fact, reconcile a fleet, satisfy the supervision gate, authorize interruption, or accept tasks. The lead must still apply the separate handoff and supervision rules. New unresolved knowledge after the stow requires a new stow id.

## Persistence contract

Owner and sole writer: `herdr-teamlead`. Its memory commands work without configuration, Herdr, dispatch-state reads or worker contact. Other readers may read the documented schema but never migrate or edit it. Memory reads create no files, take no lock, and never refresh evidence or verification timestamps.

Storage is `<canonical-selected-state-path>.memory/index.json`; the selected `--state` path is expanded and resolved before deriving the sidecar. Symlink aliases therefore share a history. The owner appends immutable records under the existing OS-backed state lock and atomically replaces the index. A failed replacement preserves the previous committed document. Exact retry is idempotent. Conflicting ids, stale revision parents, unsupported schemas and corrupt history fail visibly without overwriting existing records. No old schema exists yet; any future shape change needs an owner migration and version bump.

The index has `schema_version`, `state_path` and ordered `records`. Both its schema and every record's schema are version 1. All record kinds share `id`, `kind`, `recorded_at` and `input_digest`; `recorded_at` is an injected UTC timestamp and `input_digest` binds the original JSON input. Record ids are unique across kinds.

Lesson records have `kind: "lesson"` and the lesson input fields above, replacing `sources` with captured source records. A file source has `schema_version`, `kind: "file"`, canonical absolute `path`, `sha256` and byte `size`. An HTTPS source has `schema_version`, `kind: "url"` and `url`. Each lesson's `supersedes` chain points to its prior revision. Its first revision is active. Verification times cannot follow recording; an optional expiry must follow verification.

Stow records have `kind: "stow"` and the stow input fields above, replacing `required_reads` with file receipts in the supplied order. Captures and unresolved knowledge remain in the index even if those source files later disappear.

Reader output adds observations to copies of saved sources: `same_bytes`, `changed`, `unavailable`, or `not_checked_offline` for URLs. Lesson views add `expired` and `use_requires_live_verification: true`. Stow views add `reset_ready` and `readiness_scope: "local_capture_only"`. These are computed views, never saved fields. Read commands return `memory_path` and `checked_at`; write commands return `memory_path` and `replayed` alongside the record. None of these fields is task acceptance evidence.
