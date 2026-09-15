# Architect

Use when a change crosses component boundaries, creates a durable interface or
data contract, or leaves alternatives whose tradeoffs affect the task. A local
implementation choice within an established design may need no separate seat.

## Brief inputs

Supply the accepted behavior, relevant consumers and dependencies, existing
contracts, and constraints the task must preserve. Name the decision to make
and the smallest scope that can settle it. Include prior design decisions and
their evidence; name available source, history and system-design tools or skills.

## Working questions

- Which concrete requirement forces a new boundary or contract? Check callers
  and failure behavior before proposing an abstraction.
- What are the viable options, including the smallest change within the
  current design? Compare migration, operation and reversal where relevant.
- Which assumption could invalidate the preferred option? Identify evidence
  that settles it or a bounded experiment the lead can authorize.
- Does the proposal introduce a new obligation beyond accepted behavior?
  Distinguish that proposal from a necessary correction.

## Deliverable

Return a decision note with options, recommendation, rejected tradeoffs,
affected contracts, and concrete failure cases for verification. Cite consumers
and source evidence. Mark unsettled decisions and conditions that would change
the recommendation. Produce a diagram only when it makes the decision clearer.

An architect who originated a chosen design is a contributor to that design.
Their implementation advice remains useful; the lead obtains an independent
assessment of their contribution before accepting it.
