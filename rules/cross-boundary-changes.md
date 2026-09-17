---
alwaysApply: true
description: A change that crosses a module, service, or contract boundary is mapped before it is edited — producer, consumers, contract, verification per boundary — and shipped one coherent change at a time
---

# Cross-Boundary Changes

## Map Before Editing

- A change touching a shared contract — an API, a schema, a message shape, a config key, a public type — is a cross-boundary change
- Before editing, map every boundary the change crosses: the producer, each consumer, the contract between them, and the command that verifies that boundary
- Read the nearest instruction file (`AGENTS.md`, `CLAUDE.md`) in every affected module, plus the callers, the contract types, and the tests
- State the map before the first edit — a few lines, not a design document
- A consumer the map missed is a finding against the map, not a surprise to absorb silently

## Compatibility

- A contract change that could break a consumer resolves compatibility explicitly: version the contract, keep the old shape readable, or migrate consumers in a staged sequence
- Prefer a staged migration — new shape accepted, consumers moved, old shape dropped — over a single cut when consumers deploy separately
- Instruction files that conflict across modules are cited, both sides, in the report; never pick one silently (`rules/ship-on-green.md` Murky)

## One Coherent Change

- Implement one coherent change at a time; unrelated cleanup stays out (`rules/boy-scout.md` governs what to file instead)
- Run the narrow tests for each touched module first, then the integration or repository-wide check
- Report every boundary whose verification did not run, and its consequence (`rules/verify-before-done.md` Report Faithfully)
- Split into several PRs only when the task needs it and the user authorized it
