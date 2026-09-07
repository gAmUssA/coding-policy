---
alwaysApply: true
---

# Hook Action Reporting

## Surface the Status

- A SessionStart hook emits an `additionalContext` payload beginning with `Session-start status — ` only when it has a status to report
- Many outcomes emit nothing (non-repo, throttled freshness check, no matching dependency)
- At session start, collect every such payload the context carries (there may be none) and relay each in full as one block, before the first substantive action
- A payload may span several lines, a marker line plus continuation lines such as an update list or a `NOTE:`
- Relay the whole payload, never only its marker line
- Report the block once per session, at the start, never repeating it every turn
- Relay the payloads verbatim, without padding or restating them as your own analysis

## Act on What It Names

- A payload may name an action: a branch to sync, a pinned dependency to fix, an update that failed
- After relaying the block, act on any such action per its governing rule
- An all-green status needs only the report, no further action

## Reconciliation With `response-clarity`

- Narrow exception for the session-start status block to `rules/response-clarity.md` Lead With the Action.
- Preconditions (all required):
  1. The block appears once per session, at the start, before the first substantive action
  2. It carries only the `Session-start status — ` payloads, nothing else
- Every other turn opens with the action, no status block
