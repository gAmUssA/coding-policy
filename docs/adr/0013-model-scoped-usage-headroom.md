# Usage Headroom Is Scoped to the Model a Seat Runs

## Status

accepted

## Date

2026-10-23

## Context

Claude's `/usage` prints a per-model weekly window, `Current week (Fable)`, beside `Current week (all models)` and `Current session`. `herdr-teamlead` computed a seat's `headroom_pct` as the minimum across every parsed window. Once the Fable-only window was spent, every Claude seat on the account read 0%, an Opus 5.5 judge included, and `plan` halted every judge round (#69). Judge affordability takes the minimum across a shared `window_group`, so scoping the judge's own record alone would not have helped: a Fable pool-mate still halted it.

Options considered:

- **Drop per-model windows entirely.** This under-reports a seat that really runs the exhausted model.
- **Scope once at measure time by `model_label`.** This is simple, but a round's tier row or the judge pin can seat a worker on another model.
- **Scope at measure time by `model_label`, and let the planner re-read the stored `windows` under the model each seat runs this round.** Chosen.

## Decision

- `parsers.headroom_pct(windows, model)` counts a `Current week (<Model>)` window only when `model` contains that family as a whole word, compared case-insensitively. `Current session`, `Current week (all models)` and every codex and grok window bind every seat. An empty `model` keeps every window.
- `measure` scopes `headroom_pct` to the agent's `model_label` and records it as `headroom_model`. `windows` keeps every parsed window.
- Snapshots move to `schema_version` 4. The owner's `3 → 4` migration sets `headroom_model` to `""` unconditionally and keeps the version-3 all-window minimum. That is the version-4 reading for an unknown model.
- `plan` re-reads each record's stored `windows` under the model the seat runs in that role: the pinned `judge.model` for the judge, and the tier row's `model` otherwise. The re-read applies to ranking and to judge affordability across the pool. A record with no usable `windows`, or a seat with no known model, keeps its recorded `headroom_pct`.

## Consequences

- A spent per-model window binds only seats that run that model. An Opus judge on an account whose Fable window is spent is seated while `Current week (all models)` covers the ruling.
- `windows` is load-bearing for the planner. Its shape and meaning must stay the same across snapshot versions, or every reader that re-reads it must move with a new version.
- A new agent kind with a per-model window needs its label recognised by `parsers._window_model`. Until then, its windows bind every seat, which is the conservative reading.
- A seat with no `model_label` and no tier model still reads the all-window minimum.
