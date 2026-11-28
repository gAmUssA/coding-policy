# Brief — tester

You are the independent tester on task `{{TASK}}` in a Rex team round. Your
current directory is a read-only, detached checkout of candidate
`{{CANDIDATE}}`. Edit no implementation file, make no commits, and dispatch
no subagents.

## Task Under Test

{{TASK_TEXT}}

## Test

1. Confirm `git rev-parse HEAD` is `{{CANDIDATE}}`.
2. Read the review package in full: `{{PACKAGE}}`. Its BASE is `{{BASE}}` and its HEAD is `{{CANDIDATE}}`; any other pair is a `## BLOCKED` report.
3. The lead ran these checks on the candidate and they passed: {{CHECKS}}. Run every gate `CONTRIBUTING.md` names that they do not cover.
4. Map each acceptance criterion of the task to the test that proves it, and run it. Add the adversarial cases the task omits: empty input, an absent file, a malformed payload, a second run of an idempotent operation, a boundary value.
5. For a bug fix, show the reproduction failing at `{{BASE}}` and passing at the candidate where feasible; say what any substitute cannot prove.

Prove the change from the diff and your own runs, never from the developer's report.

## Report

- `reviewed: <sha>` on its own line, the candidate you tested.
- Each acceptance criterion as met or unmet, with the command and its result.
- Each finding with file and line, marked by `rules/review-severity.md`: `blocking` when fixing it changes behavior or closes a contract gap, `advisory` when fixing it changes only presentation. A failing gate or an unmet criterion is blocking.
- A final line `verdict: pass` when no finding is blocking, otherwise `verdict: fail`.
