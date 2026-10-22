# Herdr Lead Reads the Team Contract on Demand

## Status

accepted

## Date

2026-10-22

## Context

`rules/agent-team-operation.md` made the Herdr lead read `skills/herdr-teamlead/references/team-operation.md` in full before planning a round, and the `herdr-teamlead` preamble repeated it. The file is about 32 KB, and most of it governs steps a given round never reaches: the judge seat, fix loops, stalls and specialist consultations. The operator runs Herdr teams regularly, so that read is paid on every round. Upstream jbaruch/herdr-policy dropped the preload in 64a8082: a small operating context loads first and the detailed contract is consulted for the current question.

Options considered:

- **Keep the full read.** Simple, but every round pays for sections it never uses.
- **Split the contract into one file per section.** Each read is a whole file, but about forty pointers of the form "`team-operation.md` <Section>" across rules, references, hooks and Python docstrings would break or drift.
- **Keep one file, put a small core first, and name each other section at the skill step that needs it.** Every existing pointer stays valid.

## Decision

Keep `team-operation.md` as the single contract. Its core is every section above Team Composition: Reading This Contract, Two Modes, Lead Seat, Reports, Authority and Policy, User Attention, Fleet Supervision. The lead reads the core before Step 1 and every other section at the step that names it, in a typed `text` block of `<path> <Section>` lines. Every section still binds every round, read yet or not. `skills/herdr-teamlead/tests/test_skill_invocations.sh` guard 7 refuses a pointer that does not land on a real heading.

## Consequences

- The lead's required read before Step 1 drops from the whole contract to its core.
- A new contract section must be named by the step that acts under it, or sit in the core.
- Renaming a contract heading breaks the `herdr-teamlead` step pointers, and guard 7 fails until they are updated.
- A section a round needs but no step names is still governed by the "read its governing section first" bullet in Reading This Contract.
