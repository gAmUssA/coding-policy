# Rex Team Lead State Schema

Owner: `skills/rex-teamlead/rexlead/team.py` (the `team` command). It is the
only writer and the only reader. One record per task at
`$REX_TEAM_HOME/<task>.json` (default `~/.rex-team/<task>.json`), written
whole and atomically through a temporary file and `os.replace`. Report files
live in `$REX_TEAM_HOME/<task>/reports/`.

## Record, `schema_version` 1

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `1` |
| `task` | string | Task id, `[a-z][a-z0-9-]{0,30}` |
| `repo` | string | Absolute path of the repository the round works on |
| `base` | string | Commit the round started from (`HEAD` at `up`) |
| `session` | string | The task's Rex session id (`session:<uuid>`) |
| `workers` | object | Role name → worker, below |

Worker:

| Field | Type | Meaning |
| --- | --- | --- |
| `agent` | string | `claude`, `codex` or `grok` |
| `model`, `effort` | string or null | Launch flags given to `spawn` |
| `block` | string | The worker's Rex block id (`block:<uuid>`) |
| `worktree` | string | Absolute worktree path |
| `branch` | string or null | `team/<task>-<role>` for a writer; null for a read-only checkout |
| `commit` | string | Commit the worktree started at |
| `dispatches` | array | Each `assign`: `{nonce, report, sent_at}`; the last is current |

`sent_at` is an ISO-8601 UTC timestamp. A worker's program status counts
toward `wait` only when its `updated_at` is at or after the current
dispatch's `sent_at`.

## Contract

- A record whose `schema_version` is not `1` is refused with `state_version`; nothing migrates it.
- A missing record reads as "no task", answered with `no_task` and the `team up` command to run.
- The record is a last-seen snapshot. `up` re-checks the session is live in Rex, and `assign` re-reads the worker's live status before any input.
- `down` removes a worker from the record and closes its tab; its worktree and reports stay.
