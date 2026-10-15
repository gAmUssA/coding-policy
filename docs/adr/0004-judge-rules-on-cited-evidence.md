# Judge Rules on Cited Evidence

## Status

accepted

## Date

2026-10-15

## Context

In adjudication mode the Herdr judge read both positions and then verified "the disputed facts against the tree itself". The judge seat runs on the most capable model at its highest effort, so open-ended exploration there was the costliest work in a round. Upstream jbaruch/coding-policy measured it as such ("open-ended archaeology on top-tier tokens") and changed the contract in `8285c07`. A judge with no limit on what it may read also has no limit on what it may base a ruling on, so two judges can rule differently on the same dispute.

Options considered:

1. Keep tree verification and cap it by a token or time budget.
2. Let the judge rule only on what each position cites. Route a dispute whose cited evidence cannot decide it to the investigator, then back to the judge.
3. Drop verification and let the judge rule on the positions' prose.

## Decision

Option 2. In adjudication, each position cites its evidence: file and line, a command with its output, or a revision. The lead copies those citations into the judge brief and adds none of its own. The judge opens only what a citation names.

The ruling set gains `insufficient — <facts needed>`. On it, the lead consults the investigator for those facts and re-dispatches the judge on the same dispute with the investigator's report. `insufficient` is not a completed ruling: no checkpoint may cite it, and `teamlead/recovery.py` refuses one that tries. `blocked` stays reserved for a question only the operator can answer. Diagnosis mode is unchanged.

## Consequences

- A judge round costs what its citations cost, not what the tree costs.
- The same citations yield the same ruling.
- Fact-finding moves to the investigator, which runs on a cheaper seat and already owns reproduction and evidence-gathering.
- A dispute can take an extra round-trip (judge, investigator, judge) when the positions cite too little. Positions that cite well avoid it.
- Later work must keep three things true: the lead never adds its own evidence to a judge brief, `insufficient` never stands as a ruling, and diagnosis mode's contract stays separate.
