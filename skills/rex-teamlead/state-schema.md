# Rex Team Lead State Schema

Owner: `skills/rex-teamlead/rexlead/team.py` (the `team` command). It is the
only writer and the only reader. One record per task at
`$REX_TEAM_HOME/<task>.json` (default `~/.rex-team/<task>.json`), written
whole and atomically through a temporary file and `os.replace`. Report files
live in `$REX_TEAM_HOME/<task>/reports/`; `check` logs live in
`$REX_TEAM_HOME/<task>/checks/<role>-<k>/<i>.log`, one directory per `check`
and one log per `--run`, numbered from 1.

## Record, `schema_version` 2

Every field is required unless marked optional; none has a default. The
writer writes every field on every save.

| Field | Type | Meaning |
| --- | --- | --- |
| `schema_version` | integer | `2` |
| `task` | string | Task id, `[a-z][a-z0-9-]{0,30}` |
| `repo` | string | Absolute path of the repository the round works on |
| `base` | string | Commit the round started from (`HEAD` at `up`) |
| `session` | string | The task's Rex session id (`session:<uuid>`) |
| `workers` | object | Role name → worker, below |

Worker:

| Field | Type | Meaning |
| --- | --- | --- |
| `agent` | string | `claude`, `codex` or `grok` |
| `model`, `effort` | string or null, optional | Launch flags given to `spawn`; absent or null means the agent's default |
| `window` | string | The worker's Rex window (tab) id (`window:<uuid>`) |
| `block` | string | The worker's Rex block id (`block:<uuid>`) |
| `worktree` | string | Absolute worktree path |
| `branch` | string or null, optional | `team/<task>-<role>` for a writer; absent or null for a read-only checkout |
| `commit` | string | Commit the worktree started at |
| `dispatches` | array | Each `assign`: `{nonce, report, sent_at}`; the last is current |
| `check` | object or null, optional | The latest `check` of a writer: `{candidate, verdict, checked_at}`; absent or null means none ran. Added in `schema_version` 2 |

`check.candidate` is the commit the runs tested, `check.verdict` is `pass`,
`fail` or `invalid`, and `check.checked_at` follows the `sent_at` format below.

`sent_at` is an ISO-8601 date and time with a UTC offset (`+00:00`); the
reader refuses a value without a time, without an offset, or at another
offset. The record's `task` must equal the task it is stored under. A worker's program status counts
toward `wait` only when its `updated_at` is at or after the current
dispatch's `sent_at`.

## Naming

Every name is derived from the task id and the role, so a session opened on
any Rex client, Rex for iOS or a remote viewer included, reads at a glance.
Rex draws each tab's live state (working, blocked, done) itself; the labels
add who the tab is and how its last assignment ended.

| Rex object | Label | Set by |
| --- | --- | --- |
| Session | `team-<task>` | `up` |
| The session's own shell tab | `shell · <repo>` | `up` |
| Tab (window) | `<role> · <agent>` | `spawn` |
| Tab after a dispatch | `<role> · <agent> · #<n>` | `assign`, `n` counting that worker's dispatches |
| Tab after `wait` | `<role> · <agent> · #<n> delivered`, `· #<n> no report`, `· #<n> blocked: <kind>` or `· #<n> error` | `wait` |
| Tab after `check` | `<role> · <agent> · #<n> checks pass`, `· #<n> checks fail` or `· #<n> checks invalid`; no `#<n>` before the first dispatch | `check` |
| Pane (block) | `<task>-<role>`, the stable handle for `rex -b` | `spawn` |
| Branch | `team/<task>-<role>` (writers only) | `spawn` |
| Worktree | `~/.worktrees/<repo>/<task>-<role>` | `spawn` |

A tab label is cosmetic: a failure to set it comes back as `tab_label_error`
in the command's JSON and never fails the command. Roles are lowercase
(`dev`, `review`, `review-2`), so a tab never needs more than its role, its
agent and its latest outcome.

## Contract

- `2` adds the optional worker `check`, an additive change.
- A `schema_version` 1 record is upgraded by the owner on read: it is stamped `2`, each worker gets `check: null`, and it is rewritten before the command continues. A version-1 worker carrying `check` was written by no plugin and is refused with `state_corrupt`.
- A record with a higher integer `schema_version` was written by a newer plugin; the reader refuses it with `state_newer` and the instruction to update coding-policy, and writes nothing.
- Any other `schema_version`, a required field that is missing or of the wrong type, a dispatch without `nonce`, `report` and `sent_at`, or a `check` without `candidate`, a known `verdict` and a UTC `checked_at`, is refused with `state_corrupt`, naming the field; the reader writes nothing.
- A missing record reads as "no task", answered with `no_task` and the `team up` command to run.
- The record is a last-seen snapshot. `up` re-checks the session is live in Rex, and `assign` re-reads the worker's live status before any input.
- `check` writes the worker's `check` only after its runs are classified; a refused `check` writes nothing. A recorded `check` reports what ran at `candidate`, never that the branch is still there.
- `up` on a task whose session is gone opens a new session and keeps the base and the workers; their tabs are gone until `down` forgets them.
- `spawn` of a role whose worker is live reconnects to it; on a path left by an earlier spawn it adopts that worktree; a failed launch removes the worktree and branch it created.
- `down` closes each live tab, removes a read-only checkout with `--force` only after `git worktree list` shows it is still detached at the recorded `commit`, keeps a writer's worktree, and forgets each worker once its own cleanup succeeded; a checkout it cannot remove keeps its worker recorded and fails the command with `worktree_remove_failed`, so the next `down` retries it. Reports stay.
