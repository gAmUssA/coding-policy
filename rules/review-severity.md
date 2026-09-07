---
alwaysApply: true
description: Review findings carry a severity — blocking gates the merge, advisory never does; read every finding, act by severity.
---

# Review Severity

## Two Tiers

- Every review finding is **blocking** or **advisory**
- The test is behavioral — does fixing the finding change what an agent or the pipeline does?
- Blocking: the fix changes behavior or closes a contract gap
- Advisory: the fix changes only presentation

## Blocking — Gates the Merge

- Correctness and security defects
- Policy-contract violations: a carve-out's unmet preconditions, `no-secrets`, `ci-safety` gate-evasion, surface-sync that breaks publish
- A rule directive whose violation changes agent behavior
- A style finding whose fix changes meaning — an atomic-bullet split that alters what the bullet directs

## Advisory — Never Gates

- Pure prose and style: connective or em-dash placement, a presentation-only atomic-bullet split
- CHANGELOG wording, naming taste, synonym preference
- Copilot findings are always advisory regardless of Copilot's review state — even a Copilot `CHANGES_REQUESTED` never gates the agent's flow
- Anything whose fix changes only presentation, not behavior

## Gating Predicate

- Any blocking finding present → the reviewer posts `CHANGES_REQUESTED` and the merge gates
- Only advisory findings → the reviewer posts `COMMENTED` and the merge is allowed
- The policy reviewer's posted state already encodes this — the event is derived from per-finding severity (see the `skills/onboard-repo/templates/post-review.sh` header; consumers carry a copy at `.github/codex-review/post-review.sh`)
- The merge watcher gates on the policy reviewer's `CHANGES_REQUESTED` alone (see `skills/release/watch-pr-reviews.sh` header)
- Copilot never gates

## Split Reading From Acting

- Read every finding in full first — severity never licenses skipping a body (see `rules/reviewer-feedback-reading.md`)
- Blocking → fix before merge
- Advisory → acknowledge
- Fold an advisory in only when a blocking round is already happening
- Otherwise defer the advisory to a follow-up PR or issue and reference it from the current PR
- Never burn a dedicated re-review round on a lone advisory
