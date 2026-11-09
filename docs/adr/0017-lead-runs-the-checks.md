# Lead Runs the Checks

## Status

proposed

## Date

2026-11-08

## Context

In a Rex team round (ADR 0016) a developer's delivery is its program status reaching `done` plus a report carrying the dispatch's nonce. That proves the worker stopped and answered this dispatch. It does not prove the work is right: the report's test results are the worker's own word, and the reviewer re-ran the suite on its read-only checkout.

andrebrov/agent-factory-lab experiment 10 set validity rules for agent pipelines. A result counts only when the orchestrator observed it, "never on another agent's word". A run that cannot reach a verdict is invalid, never a failure of the code under test.

Options considered:

- **Trust the report.** The lead reads the developer's test lines. Cheap, and exactly the word the rule refuses.
- **Leave the checks to the reviewer.** The reviewer runs the suite. A broken environment then reads as a review finding and enters the fix loop as a code defect.
- **The lead runs the checks** before the reviewer, in the developer's worktree, with commands the lead chose.

## Decision

Add `rex-team.sh check`. The lead names each command with `--run`; nothing is read from a worker's report. The command refuses with `check_mismatch` unless the writer's branch tip and its worktree `HEAD` are the candidate, and with `check_dirty` when the worktree has uncommitted or untracked changes. Each command runs with `bash -c` in its own session, with no stdin, its stdout and stderr discarded, and a per-command timeout. Exit 0 is `pass` and any non-zero exit is `fail`. A timeout, a signal, `bash` failing to start, or a run that moved `HEAD` or left the tree dirty is `invalid`. Once the command's leader exits, its process group is sent `SIGKILL`, so nothing it started in the background outlives the run. The verdict is data: exit 0 once the runs are classified. The lead passes the repository's own check entry points, the commands its CI workflow, `CLAUDE.md` or `AGENTS.md` names, verbatim; it never composes, wraps or rewrites a command.

The skill gates the reviewer on `pass`. `fail` returns to the developer with each failed command and its exit status; the developer reruns it in its own worktree to see the output. `invalid` goes to the operator and never enters the fix loop. The reviewer may rely on the lead's passed check. The worker's latest check is recorded in the state record, which moves to `schema_version` 2.

## Consequences

- "Delivered" now also means "verified by the lead" before any reviewer spends a turn.
- A broken environment surfaces as `invalid` with its reason, not as a code defect.
- The lead picks the check commands at Step 1 and owns them; a wrong pick yields a wrong `pass`.
- A check outlasts an agent lead's shell-tool limit at the default budget; the lead runs it with a longer limit or in the background.
- `check` classifies by exit status and process state only and never reads test results.
- A test command's own guarantees, such as failing when it runs zero tests, belong to that repository's test entry point under its `testing-standards`.
- No command output is kept: nothing a command prints is persisted, so there is no log to redact.
- `check` kills processes a command leaves in the background but does not report them, so such a run can still `pass`. A process that leaves the group with `setsid` survives.
- A timed-out command's whole process group is killed.
- The clean-tree reads before and after the runs compare content against the candidate, never `git status`: `HEAD` is read into a temporary index in the checkout's own git directory, never in the worktree whatever `TMPDIR` says, and the working tree staged into it, so cached stat data, index flags and status settings cannot hide a change. The real index is never written. An untracked nested repository is a change, named by its path.
- Paths ignored through `.gitignore`, `info/exclude` or `core.excludesFile` are exempt; clean filters and `core.autocrlf` stay honored.
- Submodules and sparse checkouts are unsupported: a candidate whose tree holds a gitlink, or a sparse checkout, is refused with `check_unsupported` before any run, and the lead takes it to the operator.
- A check with N `--run` commands hashes every non-ignored file N+1 times: once before the runs and once after each. On this repository, 390 files, `check --run true` took about 0.22 s; a large tree and more commands pay proportionally more.
- Later work: JUnit or other structured result parsing, known-red fixture controls proving a check can fail, a findings ledger across rounds, submodule support, and reporting background processes, which needs `os.waitid` (macOS Python 3.13+) and a process listing.
