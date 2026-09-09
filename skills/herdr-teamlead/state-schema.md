# Team-Lead State Schema

Schemas for the cross-invocation artifacts owned by `herdr-teamlead`, per
`rules/stateful-artifacts.md`. The Python utility alone writes and migrates
`state.json`. The lead maintains the separate Markdown task ledger; the utility
does not parse or update that document. The lead drafts retrospective synthesis;
the utility alone records the saved notes and their separate index.

## Artifacts

| Path | Owner | Purpose |
| ---- | ----- | ------- |
| `$XDG_STATE_HOME/teamlead/state.json` (default `~/.local/state/teamlead/state.json`, override `--state FILE`) | `skills/herdr-teamlead/teamlead/state.py` and its `recovery.py` helper, within the same owner skill | Snapshots, append-only assignments, and audited task recovery |
| `$XDG_CONFIG_HOME/teamlead/config.json` (default `~/.config/teamlead/config.json`, override `--config FILE`) | the operator | Per-agent usage / clear commands; teamlead reads it and never writes it |
| `<task-reports-dir>/TASK-LEDGER.md` | `herdr-teamlead`, written by the lead | Evidence-backed assignment acceptance and task completion across rounds |
| `<canonical-state-path>.retrospectives/` | `herdr-teamlead`, through its retrospective utility | Immutable retrospective notes, versioned index, and transition coverage |

The JSON formats and utility contracts below apply to `state.json` and config.
The Markdown ledger has its own contract in Task Ledger below; adding it changes
none of the existing JSON record shapes or versions.
The retrospective sidecar also leaves `state.json` and assignment versions
unchanged. Its canonical state path is the expanded, resolved path selected by
`--state` or the existing default; separate state files have separate histories.

`skills/herdr-teamlead/config.example.json` is an example to adapt and commission before live tier use. Config schema 2
adds per-agent `tiers` and `launch_args`; schema 1 remains readable without tiers.
See `skills/herdr-teamlead/references/model-tiers.md` for qualification and billing evidence. A missing config is refused with the exact `cp` command to run. The
optional `idle_markers` / `working_markers` per-agent keys carry the footer
signatures the stale-state probe reads; an agent with neither is never probed.
`slash_delivery` picks how that worker's slash commands go out, `paste` or
`type`; `dialog_next_tab_keys` names the keys that cycle a usage dialog's tabs;
`composer_glyph`, `composer_ignore_dim`, `composer_placeholders`, and
`recover_keys` drive the consumed-command check, the ghost-text and placeholder
exemptions, and the guarded one-shot recovery of a stuck composer.
`model_label` is cosmetic: it names the model on the worker's pane after a
dispatch. All are documented in
`skills/herdr-teamlead/references/herdr.md`.

`window_group` names the usage window an agent shares with other agents: two
workers authenticating as one subscription declare the same value, `measure`
copies it onto each record (snapshot `schema_version` 3), and `plan` charges a seat's cost against every
worker in that window. An agent that declares none has a window to itself.

The optional top-level `judge` key pins the judge agent, model, and effort.
Plan schema 4 echoes them in a `judge` object; a plan without that seat omits
it. Model and effort become explicit launch flags. Legacy `banner_pattern`
values are ignored: proof comes from launch or live process argv. The planner
never ranks the judge seat or gives its pinned worker another role.

Plan schema 4 also carries `tiers` keyed by role and `rounds` with the lead's
round type and context inputs. Default planning excludes unqualified tiers;
`--preview-tiers` inspects candidates before qualification. Live apply always
checks current qualification. Legacy non-tiered assignments have no tier
metadata. The operator's tier table, supported flags, qualification schema,
and billing evidence are documented in `references/model-tiers.md`.
`task_context` is null for an unlabelled plan, otherwise an object containing
`task`, cumulative `fix_round`, correction `plan` identity or null, and `work`
bounds or null. Apply refuses different task context. Earlier plan shapes and
plain role mappings remain accepted; live apply still checks current history,
allowance, tiers, qualification, and readiness. Apply output schema 6 includes
`context_transition`, persistent `dispatch_id` for labelled assignments, and
`replayed: true` when returning an existing completed result.
Version 6 adds the verified role-clear transition. Version 5 adds verified hand-release and historical-correction transition
variants; version 4 introduced the original recovery fields.

The optional `role_costs` key is the second:
`{"<role>": <number>}`, what one round in that seat is expected to
burn out of a worker's remaining headroom percentage. It overrides the
planner's own weights one role at a time, and a role it omits keeps the
default (see `skills/herdr-teamlead/references/round-setup.md`, Step 5).
A missing map means no overrides; a value that is not a non-negative finite
number is refused, naming the file and the role. `plan` is the only reader.

## Task Ledger

Choose one absolute task reports directory outside the shared checkout and
worker worktrees. Keep `TASK-LEDGER.md` there across fixes, releases, and resumes.
Record its absolute path in the saved task authorization context and the lead's
handoff before first dispatch. Do not move or delete it during worktree cleanup.
It replaces the informal round log, not the utility's dispatch/recovery ledger.

The Markdown document's frontmatter contains `schema_version: 1`, the stable
`task`, full original `base_revision`, and the absolute `dispatch_state` path
of the utility ledger. Each appended event is a Markdown section with these
required fields; unavailable values are the literal `unknown`, never guesses:

| Field | Meaning |
| --- | --- |
| `schema_version` | `1` on every event |
| `id`, `at` | Unique event identity and timezone-qualified observation time |
| `subject` | `task` or `assignment` |
| `dispatch_id`, `worker`, `role` | Actual utility dispatch identity and assigned worker/role; `not_applicable` for task events |
| `report` | Absolute report path, or `unknown` before it is known |
| `observed` | Source-attributed dispatch result, wait result, worker claim, or Herdr state; never an acceptance decision |
| `decision` | Lead assessment using the status vocabulary in `references/task-ledger.md` |
| `head_revision` | Full inspected commit SHA, `unknown` when unverified, or `not_applicable` for work without a VCS artifact |
| `evidence` | Absolute report/artifact paths with the inspected content or digest, VCS refs, and gate/run URLs with their observed results; `unknown` when none exists |
| `assessment` | Why this decision follows from the evidence, remaining criteria, and the next action |

The task identity and base in the document apply to every event. Append a new
decision when evidence changes; preserve earlier records. Event sections may
contain prose under `assessment` for the lead's reasoning. This is a human-readable
decision log, not a new machine status API or an input to `teamlead.sh apply`.

- **Writer** — the lead running `herdr-teamlead` writes after dispatch, after
  every wait outcome, after report assessment, and before any pause or handoff.
  It also records gate changes, judge decisions, release evidence, and cleanup.
  One active lead writes a task ledger; transfer ownership explicitly on handoff.
- **Readers** — a resumed lead and `herdr-standup` read schema 1 without changing
  its meaning. Workers never write it. Standup reads it without migration and
  labels unaccepted worker claims as reported; it grants no completion status.
- **Authority** — decisions refer to inspected evidence. Revalidate sources
  before acting on a recalled entry. The document grants no authorization,
  extra correction allowance, dispatch retry, or waiver of a gate. The utility
  remains authoritative for its recorded dispatches, counters, and recovery.
- **Missing, corrupt, or unsupported** — preserve any existing file and treat
  it as no usable prior acceptance. Reconcile utility history, live worker
  evidence, reports, VCS, and applicable external gates before continuing.
  No automatic redispatch or counter reset follows from a missing ledger.
- **Migration** — only `herdr-teamlead` may migrate documented older formats,
  preserving the original entries and their evidence. Version 1 is the first
  format; an unversioned round log is evidence to assess, never automatically
  accepted history. A newer format requires an updated reader. Do not overwrite
  an unreadable or newer ledger; retain it and record a recovered ledger at a
  new disclosed path after reconciling sources. Bump the document and affected
  record versions for future shape changes.

Execution guidance and the blank event template:

```text
skills/herdr-teamlead/references/task-ledger.md
```

## Retrospective Artifacts

Resolve the selected state path with `Path(...).expanduser().resolve()` and append
the literal `.retrospectives`. This directory survives task worktree and report
staging cleanup. Its owner is `herdr-teamlead`; only
`skills/herdr-teamlead/teamlead/retrospective.py` and the owner's dispatch helpers
write it. The lead supplies Markdown synthesis and source metadata through
`retro-record`, never edits its index or installed notes directly.

| File | Contract |
| --- | --- |
| `index.json` | Schema 1 object with canonical `state_path`, nullable `baseline_at`, append-only `records`, and recorded `transitions` |
| `<id>.md` | Immutable UTF-8 completed note with schema 1 metadata and the lead's substantive synthesis |
| `pending.json` | Schema 1 transaction journal with `previous_index` digest and proposed `record`; removed after the index commit |
| `index.json.lock` | Utility lock; writers acquire it after the dispatch-state lock |

Each completed `records` entry carries these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `id` | Version 1; unique lowercase identifier using letters, digits, underscores, or hyphens |
| `completed_at` | Timezone-qualified completion time normalized to UTC |
| `period_start`, `period_end` | Covered interval, ordered, reaching the check receipt's `checked_at`, and ending no later than completion |
| `triggers` | `daily`, `transition`, or both |
| `tasks` | Distinct covered task identifiers, including outgoing and proposed tasks in the checked coverage |
| `participants`, `unavailable` | Workers whose saved input was considered; unavailable worker-to-reason map, accounting for every checked worker with no overlap |
| `sources` | Receipts for inspected evidence files, each with canonical absolute `path`, SHA-256 `sha256`, and byte `size` |
| `note` | Receipt for the installed immutable Markdown bytes |
| `coverage` | Versioned worker-specific receipts binding outgoing work and the proposed transition |
| `input_digest` | Canonical metadata digest used to detect changed retries |

The saved note begins with a JSON metadata object between `---` delimiters:
`schema_version`, `id`, `completed_at`, `period_start`, `period_end`, `triggers`,
`tasks`, `participants`, `unavailable`, `sources`, and `coverage`. The lead's
Markdown follows it. Recording retries retain the original completion time.

Coverage binds the worker's latest original assignment identity, available
dispatch identity, live native/session/process evidence, report digest or stated
unavailability, and proposed role/model/effort/context and input paths. A recorded
transition links that coverage to the utility's completed boundary and replacement
identity. Later changes to another worker's assignment do not invalidate it.
The owner rechecks relevant source bytes and live identity before applying coverage;
an old note or Herdr completion label alone proves no present transition authority.
The source's nullable `dispatch_evidence` contains the original dispatch row's
`sha256` digest and nullable `report` receipt for its recorded implementation
review. That review is distinct from the worker's own report. A changed dispatch
record or review file invalidates its worker's coverage.
An unreadable known review may have a null current `report` only with the source's
explicit `unavailable` reason; its archived dispatch metadata remains bound by
`sha256`. Restored readable bytes invalidate that recorded missing condition.

Each `transitions` entry has `schema_version: 1`, unique content-derived `id`,
UTC `at`, `agent`, the original `descriptor` coverage, and the verified `incoming`
observation. The descriptor must match saved retrospective coverage or prove an
exempt first start. The transition receipt bridges only the utility's own recorded
boundary to that incoming worker; it does not cover later outgoing work.

The cadence uses the latest completed retrospective, or the established first-work
baseline when no retrospective exists. Failed checks and incomplete notes never
advance it. Existing work with no usable history is due immediately. Coverage for
a proposed transition is independent of the daily due decision.

- **Writer** — the utility validates recording metadata and required nonempty
  synthesis sections, reads source bytes, and atomically installs the completed
  note before committing the index. The lead judges the content's substance.
  Writes serialize under the sidecar lock. Identical retries preserve the existing
  record and its completion time; conflicting IDs or pending transactions fail
  with a diagnostic. A journal preserves interrupted recording for reconciliation.
- **Readers** — `retro-list` and `retro-show` accept schema 1 and verify installed
  note digests. They read without Herdr, config, dispatch-state migration, or
  writes. `retro-list --since` includes records completed at or after its cutoff;
  `retro-show` selects the latest completion unless an ID is supplied. Historical
  evidence sources may be unavailable after cleanup; the saved note remains
  retrievable. Applying its transition coverage still revalidates source evidence.
- **Missing, corrupt, or unsupported** — a missing history means no prior
  retrospective. It never proves an existing worker is new or authorizes a send.
  Preserve orphan notes, pending transactions, corrupted files, and newer formats;
  report the diagnostic and restore original bytes or update the owner. Never
  overwrite them with an empty index or manufacture completion coverage.
- **Migration** — version 1 is the first format. Only `herdr-teamlead` may migrate
  documented older formats, preserving notes and historical records. Future shape
  changes bump the document and affected record versions. An unsupported reader
  has no usable prior state and must not launch, clear, retry, or reset counters
  from that fallback.

Execution, source collection, note structure, and command inputs:

```text
skills/herdr-teamlead/references/retrospectives.md
```

## State Record Format

```json
{
  "schema_version": 5,
  "snapshots": ["<measure output>, oldest first, ring capped at 20"],
  "assignments": [
    {
      "schema_version": 5,
      "at": "2026-09-01T21:00:00+00:00",
      "role": "developer",
      "agent": "grok",
      "status": "applied",
      "cleared": false,
      "clear_reason": "retained",
      "task": "owner/repo#322",
      "fix_round": 1,
      "context_session": {"pane_id": "w4:p1", "source": "herdr:grok", "agent": "grok", "kind": "id", "value": "native-session-id"},
      "tier": null
    }
  ],
  "recovery": {
    "schema_version": 4,
    "tasks": {},
    "checkpoints": [],
    "plans": [],
    "dispatches": [],
    "context_permissions": [],
    "events": [],
    "hand_clearances": [],
    "historical_attempts": [],
    "role_clearances": [],
    "delivery_recoveries": []
  }
}
```

| Field | Type | Meaning |
| ----- | ---- | ------- |
| `schema_version` | integer | Currently `5`. Bumped on any shape change |
| `snapshots` | array | Whole `measure` documents, oldest first; the ring holds the last 20 |
| `assignments` | array | Append-only ledger of who held which role |
| `snapshots[].schema_version` | integer | Currently `3`. Version 2 added `window_group`; version 3 adds per-round `tier_billing`. Older snapshots migrate on read, preserving headroom and shared-window membership |
| `snapshots[].agents[].window_group` | string | The usage window this agent shares with others; empty means a window of its own. Present on every agent record, including skipped and failed ones — pool membership is config, not a measurement result |
| `assignments[].schema_version` | integer | The row's own version, stamped on write |
| `assignments[].at` | string | ISO-8601 timestamp, from `--now` or the CLI's clock |
| `assignments[].role` | string | The role handed out |
| `assignments[].agent` | string | The agent that received it |
| `assignments[].status` | string | `applied`, `sent_but_not_started`, or `unknown` |
| `assignments[].cleared` | boolean or null | Whether the dispatcher confirmed its automatic clear; null means historical evidence is unavailable |
| `assignments[].clear_reason` | string | `automatic` with cleared true, `hand` or `retained` with cleared false, or `unknown` with cleared null |
| `assignments[].task` | string or null | Non-empty stable task identifier; null for older or unlabelled assignments |
| `assignments[].fix_round` | positive integer or null | Task's fix number; null for initial development or non-fix work |
| `assignments[].context_session` | object or null | Verified native session reference scoped to a pane: `pane_id`, `source`, `agent`, `kind`, `value`, all non-empty strings; kind is `id` or `path`. Null means continuity was not established |

| `snapshots[].agents[].tier_billing` | object | Round → `{model, effort, window}` for configured tiers; unmeasured attribution is `unknown`. Empty for older snapshots |
| `assignments[].tier` | object or null | Requested `round`, selected config `tier_row`, `kind`, `model`, `effort`, declared/effective multipliers, billing window, launch options, input `prompt_hash`, accepted qualification summary, and `verified` proof. Null for old or non-tiered dispatches |

`verified` contains `model`, `effort`, `argv`, `source` (`launch_argv` or
`process_argv`), and `pane_id`; process proof also contains `pid`. Loading
state rechecks that the argument vector proves the recorded pair. Stored
proof never replaces checking the live process before retained dispatch.
`prompt_hash` hashes the original assignment message, common file, and brief
as length-framed byte strings; the generated metadata footer is excluded.

Every hand-off is recorded, one that never started included: the ledger is what
the tool did, and a round that went out and died is the thing worth looking up
afterwards. `status` keeps that honesty out of the plan — `role_counts` skips
rows marked `sent_but_not_started`, so an assignment nobody began never counts
as experience of the role. The skip list is a deny-list: a version 1 row
migrated forward carries `unknown` and still counts, which says the tool cannot
prove the outcome rather than that the history should vanish.

Every record carries `schema_version`, not only the document: a ledger row
outlives the document it arrived in, and a version on the row is what makes a
later migration auditable row by row. Each snapshot is a whole `measure`
document and arrives already stamped.

## Recovery records

The recovery document uses `schema_version: 4`; individual records retain their
independent versions (1, or 2 for stale-Grok delivery). The owner adds empty `role_clearances` and
`delivery_recoveries` arrays when migrating versions 1 or 2. Version 1 also
gains empty `hand_clearances` and `historical_attempts` arrays. Existing
record shapes, contents, assignment rows and evidence remain unchanged. State
and assignment schema 5 and snapshot schema 3 remain unchanged. A version-4
state migrates through its existing owner chain before either recovery command.
Every record carries `at` and `task`. Authorizations contain the actual operator
message `source` and `quote`; evidence receipts contain absolute `path` and
`sha256` of the bytes read by the owner. Receipts are audit evidence, not a
replacement for live readiness, source review, or release gates.

| Collection | Record fields and relationships |
| --- | --- |
| `tasks` | Keyed by original task identity; `task`, immutable full `base_revision`, `scope`, `allowed_paths`, `authorization`. Migration invents none of them. |
| `checkpoints` | Unique `id`, `fix_round`, original `base_revision`, concrete `defect`, `previous_attempts`, `progress`, `change_in_approach`, `judge_agent`, `judge_report`, `judge_evidence`. Requires a completed pinned-judge assignment after the preceding developer attempt. |
| `plans` | Unique `id`, `checkpoint`, original `base_revision`, `scope`, `allowed_paths`, `additional_fixes`, derived `first_fix`/`last_fix`, `authorization`; optional `supersedes` references a preserved prior approval. |
| `dispatches` | Unique `id`, byte/input `fingerprint`, `role`, `agent`, cumulative `fix_round`, `plan` or null, `work` or null, `status`, `result`, `report`, and `assignment_index` once an outcome is recorded. CLI records `brief`, `common`, `observed_before`, and `context_before_send`; reconciled retries preserve `prior_assignment_indices`. |
| `context_permissions` | Original `assignment_index`, `next_fix`, `reason`, `authorization`, `evidence`, `evidence_receipt`, later `observed_session`, and `basis: operator_authorized_fresh_handoff`. The original null session is never replaced. |
| `events` | Append-only `sequence`, `kind`, and structured `details` preserving approvals, waiting states, reservations, send transitions, results, transport retries, superseded review receipts, and recovery decisions. |
| `hand_clearances` | Unique `id`, original release `assignment_index`, `previous_developer`, complete owner `input`, clear byte `receipts`, later `observed_session` or null, and `basis: verified_required_release_clear`. Both indices retain their original rows. The later observation never substitutes for historical proof; changed or missing current IDs do not invalidate archived clear evidence. |
| `role_clearances` | Unique `id`, original `task`/`base_revision`, developer `assignment_index`, actual `clearing_assignment_index` and `clearing_dispatch`, `next_fix`, complete owner `input`, clear/authorization byte `receipts`, reused or explicit `clear_authority`, later `observed_session`, `basis: verified_authorized_role_clear`, and `grants_future_attempts: false`. The input fixes the same work and correction plan used for dispatch. Original known native proof and every earlier row remain unchanged. |
| `historical_attempts` | Unique `id`, actual `fix_round`, `previous_developer`, appended `assignment_index`, original owner `input`, authorization/transport/report byte `receipts`, inspected `vcs` checkout/head/diff evidence, `basis: completed_authorized_manual_correction`, null `native_session_proof`, `grants_future_attempts: false`, and append-only `reviews`. |
| `delivery_recoveries` | Unique `id`, original `dispatch` and `assignment_index`, owner `input`, byte `receipts` for report/negative wait/pane/visible/native source/common/brief, archived `native_session`, `found: true`, `basis: archived_native_final_source`, null `native_session_proof`, and `grants_review_approval: false`. Original null session evidence is preserved; the archived user prompt binds its delivery to the saved dispatch. |

Version 4 admits delivery record schema 2 alongside unchanged schema-1 receipts.
The new record uses `basis: archived_grok_clear_source`, preserves `native_session`
as Herdr's archived observation, and adds `source_session` with `agent: grok`,
`kind: id`, and the native updates' `value`. Its `input` and `receipts` also bind
the original `plan`. All continuity and approval fields retain their prior meaning.
Migration from version 3 changes only the enclosing version, never an old record.
The source identity is delivery evidence only, never a Herdr observation or
retained-context proof. Version-3 readers refuse version 4 without migration.

Dispatch statuses are `reserved`, `sending`, `sent_but_not_started`, `applied`,
and `not_sent`. The first three hold an unresolved slot. Only confirmed
developer assignments advance the task's fix count; a pending slot blocks a
second implementation/release dispatch for that task or worker. `applied`
results must match their referenced assignment. Extra fixes require a matching
plan and work bounds, including when a reader validates historical state.
A completed legacy manual correction instead requires its linked historical
import and original bounded authorization; that record grants no future fixes.
Its appended assignment uses the original completion time, `role: developer`,
`status: applied`, actual task/agent/count, `cleared: null`,
`clear_reason: unknown`, null session and tier. It is historical work recorded
now, not a contemporaneous owner dispatch. All assignment count readers consume
that same row; they never add the import record a second time.
Each historical review is a version-1 record with `at`, `task`, unique `id`,
original `input` (attempt/head/verdict/mode/reviewer/report), and byte `evidence`.
It grants no attempt; an existing remaining plan requires its latest blocking
receipt before another correction. A full approval cannot stand in for a
blocking finding, and scoped review cannot approve a release.

`work` contains `base_revision`, `scope`, repository-relative `paths`, and
blocking `findings`. A review receipt contains `dispatch`, `head_revision`,
`verdict`, `review_mode`, independent `reviewer`, `report`, `changed_paths`, and
`evidence`. The lead verifies the actual VCS diff before recording these fields;
the command reads the report, checks its stated head, and records its digest.
The next approved correction rechecks the preceding blocking report's bytes.
An approval receipt requires full review; tester and external gates remain
separate requirements in the skill.

`context_before_send` records clear handling, native session observation,
tier proof, and any fresh `transition`. A confirmed result's
`context_transition` names `release_handoff` with prior developer/release
indices, or `authorized_context_recovery` with the original assignment and
permission reference. `verified_hand_release_handoff` names the original
developer/release indices and `clearance` identity. `historical_correction_handoff`
names the imported developer index, `historical_attempt` identity and
`continuity: unproven`. `verified_role_clear_handoff` names the original
developer, `clearing_assignment` index and `clearance` identity. None changes
native-session proof or grants an attempt.
`reconciliation` records its own version/timestamp,
original `input`, `evidence_receipt`, and later `observed_state`/`observed_session`.
An applied recovery appends a new assignment with null contemporaneous session
proof and marks its result `recovered: true`; it preserves the original row.

Each snapshot is one `measure` document: `schema_version`, `measured_at`, an
`agents` object keyed by agent name (`kind`, `state`, `herdr_state`,
`state_source`, `pane_id`, `windows`, `credits`, `plan`, `headroom_pct`,
`skipped`), and `failed_agents`. `headroom_pct` is the minimum `remaining_pct`
across that agent's windows. `state_source` is `herdr` or `probe`, naming which
signal decided `state`; `herdr_state` carries what herdr claimed. `plan` is an
informational plan name and never feeds headroom.

## Writer / Reader Contract

- **Writer** — `measure` appends a snapshot; labelled `apply` reserves before
  clear/relaunch, persists sending before terminal input, and appends the
  confirmed or unconfirmed outcome before cosmetic labels. Owner commands
  manage task/approval/recovery records; all mutations preserve audit events.
  Writes are atomic: temp file in the same directory, `fsync`,
  `os.replace`.
- **Readers** — `plan` reads the newest snapshot plus the ledger (role history
  breaks a headroom tie), and the config's `role_costs` for its seat weights;
  `state` prints the document. Neither appends records; their shared loader performs owner migrations. Live `apply` reads the most
  recent assignment for the named worker before retaining context; Step 10
  documents the retained-dispatch contract. `status` derives budgets and paused
  implementation separately from active audit work. `apply --dry-run` reads
  current recovery bounds without writes; an older ledger requires an owner
  `state` command first. Dry-run never proves live continuity or qualification.
- **Assignment chronology** — assignment `at` records the event time; import receipt
  `at` records when the owner appended its evidence. Reads preserve original row
  indices and never reorder the audit. Chronological lookup returns the original
  row/index or refuses unknown ordering; its contract is in
  `skills/herdr-teamlead/teamlead/chronology.py` (`latest_assignment`).
- **Fix history** — live developer fixes advance the task's confirmed fix
  number even when the worker changes. An initial assignment cannot reset a
  task that already has a confirmed developer assignment. `apply` uses the ledger's task and
  outcome evidence, never pane labels, for that check.
- **Session continuity** — a fresh labelled developer dispatch reads Herdr's
  native reference after clearing and correlates it after confirmed first-prompt
  delivery, including delayed IDs. A Grok `/new` without an observed pre-clear ID
  keeps null continuity; a later Herdr ID alone cannot establish its freshness.
  The recovery reference names the delivery continuation.
  Unproven correlation is null without losing
  the confirmed dispatch. An unchanged pre-clear reference cannot prove a new conversation. A
  retained dispatch checks the recorded identity against the live source at
  readiness and immediately before sending. Missing, changed, malformed, or
  non-native identity is a refusal with no terminal writes. Other assignments
  and unlabelled development record null. The official integration must report
  native session changes; check its installation when continuity is unavailable.
- **Serialization** — CLI owner transactions use a live OS lock at the state
  path plus `.lock`, including readers that may migrate. Contention refuses
  before dispatch. The file's presence alone means nothing; an OS process
  holding its lock establishes ownership. Dry-run and worker launch write no
  ledger or lock file.
- **Absent state** — a first run has no file. Every reader treats that as no
  prior state and continues; `plan` still requires a snapshot, passed with
  `--snapshot` when the state file holds none.

## Continuity Stores

The independent continuity stores do not change this dispatch-state schema.
`herdr-teamlead` owns their shape and is their sole writer:

| Store | Canonical location | Contract |
| --- | --- | --- |
| Working lessons and lead handoffs | `<selected-state>.memory/index.json` | `skills/herdr-teamlead/references/working-memory.md`, Persistence contract |
| User attention and recorded progress | `<selected-state>.attention.json` | `skills/herdr-teamlead/references/attention.md`, Commands and files |
| Fleet observations and supervision | `<selected-state>.supervision.json` | `skills/herdr-teamlead/references/supervision.md` |

Resolve the selected state path before deriving these locations. Each store and
its records have their own schema version and lock. Their offline readers never
migrate or write dispatch state. Preserve these files during task cleanup and
include their locations in lead handoffs. A saved observation is never task
acceptance or permission to act. The referenced contracts own full field shapes,
retry and unsupported-schema behavior; only their owner commands mutate them.

### Supervision schema 1

`teamlead/supervision.py` owns `<canonical selected state>.supervision.json`.
The document has `schema_version: 1`, canonical `state_path`, nullable `binding`,
and arrays `members`, `events`, `acknowledgements`, `holds`, and `watchers`.
Each array entry, binding, refinement, resolution, disposition, and evidence
receipt carries `schema_version: 1`. Timestamps are timezone-aware ISO strings.
Readers reject unsupported versions or corrupt records without migrating or
replacing them. A missing never-bound store is empty; loss of a bound owner's
store requires recovery of its history before rebinding or writing.

- `binding`: `{schema_version, at, generation, identity, state_path}`.
  `generation` is a positive, increasing integer for native lead changes.
  `identity` contains `kind: id|path`, native `value`, canonical `cwd`,
  `herdr_env`, and `pane_id`. Path identities use an absolute native transcript
  path. The same binding is also saved at the discovery path documented in the
  supervision reference. First use saves an empty unbound owner before discovery;
  mutations require the binding to commit. Discovery is written before owner
  binding; an ahead
  generation blocks an incomplete handoff. An older native session stops being
  the lead once the owner's newer generation commits.
- `members`: `{schema_version, id, at, assignment, active, observed, resolution,
  refinements}`. `assignment` contains `{id, agent, task, report, pane_id,
  native_session}`; `id` equals the stable dispatch ID, `report` is absolute,
  and unknown pane/native identity is null. `observed` is the latest map of
  opaque observation keys to JSON values; it grants no acceptance.
  `refinements` append `{schema_version, at, pane_id, native_session}` and fill
  missing expectations only. `resolution` is null while `active: true`;
  otherwise it is `{schema_version, at, outcome, evidence}`. Only the lead's
  explicit resolve command retires an enrollment.
- `events`: `{schema_version, id, seq, at, member, kind, data}`. Sequences are
  contiguous positive integers and IDs are `event-<seq>`. `member` is an
  enrollment ID or null for fleet events. `kind` names the observation or
  scheduled recheck; `data` preserves its JSON payload. Events append only.
- `acknowledgements`: `{schema_version, at, event, outcome, evidence, pending,
  recheck_at, input_digest}`. Each event has at most one acknowledgement.
  `pending: false` requires null `recheck_at`; pending outcomes have an explicit
  or script-default future recheck timestamp. `input_digest` binds the original
  per-event command input, excluding snapshot `through`, so an exact retry
  preserves the receipt and schedule
  after evidence changes or the deadline passes. Snapshot `through` bounds
  which event IDs a command can acknowledge; later events remain pending.
- `holds`: `{schema_version, id, at, kind, resume_condition, evidence,
  dispositions, resumed_at, through, members}`. `kind` is `waiting_for_user` or
  `handoff`; `resumed_at` is null until explicitly resumed. `through` captures
  the handled event boundary and `members` hashes the active assignments.
  `dispositions` contains `{schema_version, member, outcome, evidence}` for
  every active enrollment. New events or assignments invalidate that coverage.
- `watchers`: `{schema_version, id, at, heartbeat, deadline, process, status,
  reason, ended_at}`. `process` contains positive `pid` and an `identity` digest
  of its observed start time and argv. `status` is `running|stopped`; `reason`
  and `ended_at` are null until the watch stops. Health also checks a fresh
  heartbeat and bounded deadline. A PID alone proves no live watcher.
- `evidence` arrays contain readable file receipts:
  `{schema_version: 1, path, sha256, size}` with canonical absolute `path`,
  lowercase 64-character SHA-256, and nonnegative byte `size`. They preserve
  observed bytes, never infer that a prose claim is true or an action authorized.

The native Stop reader performs local read-only checks for the exact bound lead.
It never migrates state, acknowledges events, clears attention, or marks task
completion. Its normal no-binding result applies only to a session never bound
as lead; missing or unreadable bound-owner history cannot release obligations.

## Migration

Only the owner migrates, and it reads a version in one of three directions.

- **Older** — the document, or a single ledger row, is walked up the
  `MIGRATIONS` / `RECORD_MIGRATIONS` chain in
  `skills/herdr-teamlead/teamlead/state.py`, keyed by the version being upgraded
  from, and the upgraded file is rewritten in place. A document or row carrying
  no `schema_version` reads as the pre-versioning version `0`, which is what
  gives the chain a step below `1`. The `1 → 2` step stamps `status: unknown`
  on every row written before the field existed. The `2 → 3` step preserves
  status and role history while adding `cleared: null`, `clear_reason: unknown`,
  `task: null`, `fix_round: null`, and `context_session: null`. It cannot invent evidence of a retained
  session. The `3 → 4` step adds `tier: null` and preserves all task, fix,
  status, and native-session evidence. The `4 → 5` step adds an empty recovery
  document and stamps preserved assignment rows; it never infers the original
  task base, authorization, or missing native identity. Snapshot `2 → 3` independently adds
  empty `tier_billing` maps, preserving window groups and readings. Each row is migrated even in
  a document already at the current version.
- **Newer** — this build is the lagging reader, not the migrator. The caller
  gets an empty document in memory, the file on disk is left exactly as found,
  and a warning goes to stderr. A single row stamped ahead of this build makes
  the whole document unusable rather than being dropped, so the next write
  cannot lose it.
- **Corrupt** — unparseable JSON, a non-object document, a non-array field, a
  non-object row, invalid context or tier evidence, inconsistent recovery
  relationships, or an extra fix without its recorded allowance: no usable
  prior state, treated like the newer case.
  The file is never deleted, and the warning never instructs the operator to
  discard it. Losing a snapshot ring costs one re-measure; overwriting an
  unread file costs the ledger.

A tool failure is not a version case: unreadable permissions or a directory in
the state path still raises, carrying the command that fixes it.

Bump `schema_version` for any shape change; never repurpose a field. The skill
and the utility ship in the same plugin version, so writer and readers move
together — `rules/stateful-artifacts.md` Cross-Pipeline Schema Bumps does not
apply here.

## Hints, Not Authority

- A `headroom_pct` that is not a finite number — a string, an object, `true`,
  `NaN` — reads as unknown with a warning naming the agent and the value,
  never a crash. Unknown already has a defined place in the ordering, and a
  numeric string is coerced rather than discarded.
- A snapshot is a last-seen reading, never ground truth. `plan` may run off a
  stale snapshot deliberately; planning has no side effects.
- `apply` never trusts a snapshot for an agent's lifecycle state. It re-reads
  the live agent through `herdr agent get` and refuses a `working` or `blocked`
  worker before sending a single keystroke.
