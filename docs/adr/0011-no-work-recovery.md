# Owner Receipts for Closed No-Work Retries

## Status

accepted

## Date

2026-10-22

## Context

`skills/herdr-teamlead/references/team-operation.md` Stalled Workers calls a clean, unchanged stall a `not_sent`-equivalent that may be retried. The recovery owner had no such path for an initial developer dispatch. `validate_fix_history` refused a second `fix_round: null` assignment, `reserve` retries only a `not_sent` identity, and `reconcile` refuses an `applied` row. The only way forward was fix 1, which spends a correction on work nobody did. Upstream jbaruch/coding-policy fixed the same gap in 45f98ac2 (#763), on a store (v18) and supervision model we do not share.

Options considered:

- **Relabel the applied row `not_sent`.** Rewrites transport history that the ledger treats as immutable, and loses the fact that native work may have started.
- **Infer retryability from a terminal status or a clean tree at plan time.** No durable evidence, and nothing stops a second retry.
- **An owner-written receipt in its own collection**, bound to the saved stall evidence and rechecked live. This mirrors the refusal design (0.7.6), where a receipt makes a row read as bearing no work without rewriting it.

## Decision

1. Recovery store version 10 adds `no_work_recoveries`, owned by `skills/herdr-teamlead/teamlead/no_work_recovery.py` and written only by `teamlead recover-no-work`. Only the owner migrates a version-9 store, by adding the empty collection. A version-9 store already carrying it is refused.
2. A schema-1 receipt binds the source dispatch, its assignment index, the complete owner input, the wait JSON byte receipt, the canonical real path of the stalled checkout, a digest of the source row, and the list of retry dispatch IDs.
3. The owner accepts only an applied initial developer dispatch that is the task's latest and has no review or refusal. The saved `wait-report.sh --worktree --base` stall must be `no_work` against the task's base, for the enrolled worker and report, in the single worktree the frozen developer brief names. Live state must show the pane gone, the enrollment resolved, the report absent, and the checkout clean at the base.
4. A receipt makes its source read as bearing no work, so the initial slot admits one retry of the unchanged brief. The retry needs a fresh dispatch ID, a fresh and unoccupied report path, and a fresh context. `apply` repeats the live checks before input and again before reservation. A dry run runs the static and filesystem checks only and contacts no Herdr. `reserve` binds the retry's ID into the receipt in the same save, and store validation accepts a second applied `(task, null)` row only when the receipt lists it.
5. A recovered refusal move no longer counts as the outstanding move, and a recovered operator-authorized dispatch passes its authorization to its receipt-bound retry. Provider and brief limits still apply. Store validation accepts a second use of an authorization only as that receipt-bound retry.
6. A new receipt binding takes a fresh dispatch ID; only a transport retry of an ID the receipt already lists reuses it.

## Consequences

- **Easier:** a proved no-work stall of the initial assignment is retried without spending a correction, and the evidence for it is auditable.
- **Harder:** a store written by this version is unreadable to an older plugin build. That build treats it as no usable prior state and leaves it untouched.
- **Harder:** recovery requires a developer brief composed from `skills/herdr-teamlead/templates/brief-developer.md`, whose worktree field binds the checkout. A hand-written brief without that field cannot be recovered.
- **Later work must honor:**
  - receipts are the only way a no-work row stops counting; never relabel or remove the applied source row;
  - one receipt permits at most one retry that is not `not_sent`;
  - every retry gate rechecks live state, and dry runs never contact Herdr;
  - a new reader of developer attempts uses `refused_developer_indices`/`developer_attempts`, which include recovered rows.
