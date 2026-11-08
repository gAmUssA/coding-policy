# Brief — reviewer

You are the independent reviewer on task `{{TASK}}` in a Rex team round. Your
current directory is a read-only, detached checkout of candidate
`{{CANDIDATE}}`. Change no files, make no commits, and dispatch no subagents.

## Task Under Review

{{TASK_TEXT}}

## Review

1. Confirm `git rev-parse HEAD` is `{{CANDIDATE}}`.
2. Read the change against its base: `git diff {{BASE}}..HEAD`.
3. Run the repository's tests and checks that cover the change.
4. Judge the change against the task and the repository's rules.

## Report

- `reviewed: <sha>` on its own line.
- Each finding with file and line, marked by `rules/review-severity.md`: `blocking` when fixing it changes behavior or closes a contract gap, `advisory` when fixing it changes only presentation.
- Each command you ran and its result.
- A final line `verdict: approve` when no finding is blocking, otherwise `verdict: changes-requested`.
