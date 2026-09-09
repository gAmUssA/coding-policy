---
alwaysApply: true
---

# Stateful Artifacts

## What Counts

- JSON (or similar) state files a skill writes and/or reads across invocations to maintain continuity between runs
- Distinct from the plugin's static context artifacts (rules, skills, scripts per `rules/context-artifacts.md`) — those don't change between runs; these do
- Lifecycle and packaging expectations come from `rules/context-artifacts.md`; this rule adds the stateful-specific requirements below

## Required Attributes

- A **schema** documented next to the owner skill (e.g., `skills/<name>/state-schema.md` or a JSON Schema file); no schema, no artifact
- A single **owner skill** responsible for shape changes — shared ownership means no one owns the migration
- A `schema_version` field on every record so migrations are auditable
- A **writer / reader contract** — which skills write, which read, what each promises about field presence, defaults, and format

## Hints, Not Authority

- Artifacts are last-seen snapshots, not ground truth — before acting on a recalled value, verify against the live source (API, DB, filesystem)
- Stale state is the default; assume it until proven fresh

## Migration Policy

- Bump `schema_version` for any shape change — don't repurpose a field silently
- Only the owner skill migrates: on reading an older record, detect the old `schema_version`, upgrade the record, rewrite
- Non-owner reader skills must not migrate
- On an older record, a reader treats it as read-only "no usable prior state" — the next owner-skill run upgrades it
- On a record newer than it accepts, a reader is lagging, not awaiting migration — treat it as "no usable prior state" and update the reader to accept the new version (see Cross-Pipeline Schema Bumps when writer and readers deploy through separate pipelines)

## Rename / Removal

- Renaming or removing an artifact follows surface sync (see `rules/context-artifacts.md`): update the owner skill's schema doc, update every reader skill, add a CHANGELOG entry
- Keep reader skills tolerant of missing artifacts — an artifact that was never written (first run) is indistinguishable from one that was removed; both mean "no prior state"

## Cross-Pipeline Schema Bumps

- Applies when the writer and its readers deploy through separate pipelines: different repos, registries, or release cadences
- Treat the bump as non-atomic — production runs mixed versions for the rollout window
- A single-exact-version reader gate takes its no-prior-state path (see Migration Policy) only for records stamped with a version it doesn't accept — matching-version records still read normally
- That no-prior-state fallback MUST be safe and non-disruptive — never a path that escalates work (wake-always, full recompute, alert storm)
- A zero-skew rollout requires readers that parse both old and new shapes for the window: deploy those dual-accept readers (read-only, never migrating) first → flip the writer → drop the old version from the readers' accepted set
- Additive (backward-compatible) bumps always allow dual-accept — the new reader reads old records via field defaults
- Breaking bumps allow dual-accept only when one reader can be written to parse both shapes, at higher cost
- Narrow exception for breaking bumps with no dual-parse reader
- Take the writer offline for an atomic cutover — the dual-accept sequence does not apply
- Preconditions (all required):
  1. The new shape is not readable by a reader written for the old shape (the bump is breaking)
  2. No single reader can be written to parse both shapes for the rollout window
- Every other bump — additive, or breaking with a feasible dual-parse reader — uses the zero-skew dual-accept rollout above
