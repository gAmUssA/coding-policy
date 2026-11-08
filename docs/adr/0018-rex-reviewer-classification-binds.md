# Rex Reviewer Classification Binds the Round

## Status

accepted

## Date

2026-11-08

## Context

In a Rex team round (ADR 0016) the independent reviewer reports a verdict and classifies each finding as blocking or advisory under `rules/review-severity.md`. The lead then routed the round on its own reading of those findings. It could overrule the reviewer by calling a blocking finding advisory and ship past it, or escalate an advisory into another fix loop. Either way the reviewer was independent in name only.

Options considered:

- **The lead classifies.** The lead applies the behavioral test itself. Cheap, but the agent that wants the round to finish judges the findings against it.
- **The reviewer classifies, the lead may override.** Any override returns the first option's incentive.
- **The reviewer classifies and binds; only the operator reclassifies.**

## Decision

The reviewer's verdict and its classification of each finding bind the Rex round. The lead never reclassifies a finding on its own. Only the operator reclassifies a finding the reviewer marked blocking, and that reclassification is recorded in the PR body at release. `rules/review-severity.md` Two Tiers states this for every Rex team round; `skills/rex-teamlead/SKILL.md` Step 13 routes on it.

## Consequences

- An `approve` that lists a blocking finding routes as `changes-requested`.
- A `changes-requested` that lists no blocking finding routes as `approve`, so a lone cosmetic note no longer stalls a round.
- The operator is the only override; every override is visible in the PR body.
- The lead's judgment of severity no longer enters the round; a reviewer's misclassification surfaces to the operator, not to the lead.
