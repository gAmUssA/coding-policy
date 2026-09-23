---
alwaysApply: true
description: A change that alters the architecture records the decision as an ADR in the same PR — context, decision, consequences — and supersedes rather than edits an earlier one
---

# Architecture Decisions

## What Gets a Record

- A change that alters the architecture writes an Architecture Decision Record (ADR) in the same PR
- Architecture means a choice later work must honor: a dependency or framework adopted or dropped, a contract or schema shape, a storage or messaging choice, a boundary drawn between modules or services, a build or deployment topology, a security model
- A bug fix, a refactor that preserves every contract, and a feature inside an existing design get no ADR
- Unsure whether a change qualifies: it does if reverting it a year from now would need an explanation

## Where and How

- ADRs live in `docs/adr/`, one file per decision, named `NNNN-<kebab-title>.md` with a zero-padded sequence
- Each record carries, in order: Title, Status, Date, Context, Decision, Consequences
- Status is one of `proposed`, `accepted`, `deprecated`, `superseded by NNNN`
- Context states the forces and the options considered; Decision states what was chosen; Consequences state what becomes easier, what becomes harder, and what later work must honor
- A record is short — a screen, not a design document; a longer analysis is linked, never inlined
- A repo with an existing ADR convention (a different directory, a template, `adr-tools`) keeps its own

## Records Are Immutable

- An accepted ADR is never edited into a different decision
- A reversed or changed decision gets a new ADR that names the one it supersedes, and the old one's Status becomes `superseded by NNNN`
- Fixing a typo or a broken link in an ADR is editing, changing its Decision is not

## Relationship to Other Rules

- The CHANGELOG entry (`rules/context-artifacts.md` Versioning and CHANGELOG) names the ADR; the ADR carries the reasoning
- A `cross-boundary-changes` map whose compatibility choice constrains future consumers is an ADR trigger
- `rules/boy-scout.md` applies to ADRs the way it applies to docs: a decision you observe undocumented gets filed, not ignored
