# Report Gate Lines

## Status

accepted

## Date

2026-10-15

## Context

A Herdr worker's report claimed which checks it ran in prose, and the lead read the prose to decide whether the work was done. drover (andrebrov/drover `126def9`, `9757a55`) found reports claiming more than they ran to be its most frequent recurring defect, and this repo's own policy already required the command and its result in the same report (`rules/verify-before-done.md` Report Faithfully), with nothing checking it.

Options considered:

1. Keep prose; strengthen the brief wording.
2. Have the lead re-run every gate before accepting a report.
3. A machine-readable section in the report that a deterministic parser checks before an `approved` outcome is recorded.

## Decision

Option 3. Developer, tester (Modes B and C) and reviewer reports carry a `## Gates` section, one line per gate: `- <name> exit=<code> cmd=<exact command>`, with `exit=unknown` for a gate that did not finish. `teamlead/gates.py` parses it; `recovery.record_report` refuses an `approved` verdict unless the section exists once, every line is well formed, no gate name repeats, every required gate is present, and every exit is `0`. `teamlead check-gates` exposes the same check read-only. A `blocking` verdict is unaffected, and so are `assess-specialist` (tester Mode A and consultations run no gates) and historical review imports. No state record changes shape.

## Consequences

- An approval can no longer rest on a claim the report does not state as a command and exit code.
- Every developer, tester and reviewer brief must keep asking for the section; a template that drops it makes every approval fail, which is loud rather than silent.
- Reports written before this contract cannot be recorded `approved`; they are re-run or imported through the historical path.
- Later work must keep the line format parseable by `gates.py` and update both together; which gates a reviewer must list per repo (`--require`) is open.
