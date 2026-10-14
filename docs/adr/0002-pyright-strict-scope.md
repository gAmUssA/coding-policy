# Pyright Strict Scope

## Status

accepted

## Date

2026-10-13

## Context

The Herdr port (#1) brought the `skills/herdr-teamlead` and `skills/herdr-standup` Python trees into the repo from `jbaruch/coding-policy`. Under pyright `strict` the repo reported 16,102 findings at the port and 21,890 on 2026-10-14. Under `standard` it reports none, and `scripts/run-diagnostics.sh` gates at that level. Issue #2 planned a sequenced strict adoption under `rules/language-diagnostics.md` Adopting on a Dirty Tree.

The 2026-10-14 measurement, by tree:

| Tree | Strict findings |
|------|-----------------|
| `skills/herdr-teamlead` | 21,674 |
| `skills/herdr-standup` | 125 |
| `hooks/` | 80 |
| `scripts/` | 11 |

17,650 findings (81%) are `reportUnknown*` rules. They come from JSON-shaped ledgers, transport payloads and CLI records handled as plain `dict` and `list` values. Clearing them means typed record definitions or casts across about 36,000 lines of `herdr-teamlead`, in code still synced from upstream. Every such change widens the diff the next upstream sync must reconcile. The count also grew by 5,788 between the two measurements, so a fix series would chase a moving target.

Options considered:

1. Adopt strict for the whole repo through the sequenced plan in #2.
2. Keep `standard` everywhere and close #2.
3. Adopt strict where the finding count is small enough to clear in one change. Keep `herdr-teamlead` at `standard` and record why.

## Decision

Option 3. `pyrightconfig.json` lists `hooks`, `scripts` and `skills/herdr-standup` under `strict`, and the change that does so fixes their findings to zero. `skills/herdr-teamlead` stays at `standard`, still gated at zero findings, with no rule switched off and no file excluded. Issue #2 closes on this record rather than remaining open as a standing adoption plan.

## Consequences

New first-party Python outside `herdr-teamlead` is written to strict from the start, and the strict trees cannot regress silently.

`herdr-teamlead` keeps the `standard` guarantees: unresolved imports, optional misuse, call and argument mismatches. It does not get the unknown-type coverage strict adds. Revisit the decision if `herdr-teamlead` stops syncing from upstream or gains typed record definitions. Either change makes a strict adoption a bounded series, and that adoption gets a new ADR superseding this one.

Promoting an individual strict rule repo-wide (for example `reportOptionalSubscript`) once its findings are fixed stays open. It needs no new ADR.
