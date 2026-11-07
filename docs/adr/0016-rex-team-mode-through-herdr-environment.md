# Rex Team Mode Through the Herdr Environment

## Status

proposed

## Date

2026-11-07

## Context

ADR 0015 lets the team-lead skills reach Rex through a Herdr-compatible adapter, but nothing marks a Rex terminal as part of a team round. Every mode check reads `HERDR_ENV`: `rules/agent-team-operation.md` Two Modes, the worker-role checks in `hooks/check-git-sync.sh` and `hooks/stop-handoff-hygiene.sh`, `hooks/herdr-team-status.sh`, `roster.sh`, `wait-report.sh` and the supervision Stop hook. Herdr injects `HERDR_ENV` and the pane identity into every pane it manages. Rex sets `REX_SESSION` and `REX_BLOCK` in every terminal, including the operator's ordinary work, so neither can mark a round.

Options considered:

- **Teach each check about Rex.** A second mode switch in a rule, four hooks and several scripts, each a place to drift.
- **Emulate Herdr's pane environment.** A launcher starts the lead with the variables Herdr would inject, and the adapter starts each worker the same way. Every existing check keeps reading `HERDR_ENV`.

## Decision

Emulate the environment. `skills/herdr-teamlead/rex-team.sh` requires `REX_SESSION` and `REX_BLOCK`, exports `HERDR_ENV=1`, `HERDR_PANE_ID=$REX_BLOCK`, `HERDR_WORKSPACE_ID=$REX_SESSION`, `HERDR_BIN` and `TEAMLEAD_HERDR_BIN` (the adapter) and `TEAMLEAD_BACKEND=rex`, then execs the lead. The adapter's `agent start` types each worker's command behind `env` with the same variables for that worker's block. `TEAMLEAD_BACKEND=rex` is the only Rex-specific marker, read where Herdr behavior cannot be emulated:

- `supervision-bind` refuses, since Rex records no agent session id to bind.
- `label-workspaces.sh` refuses, since one session holds the team and block labels are agent names.

## Consequences

- A Rex round reuses every hook, rule and script unchanged; the operator opts in by starting the lead through `rex-team.sh`.
- Standalone agents in Rex stay standalone: `REX_SESSION` alone never marks a round.
- A Rex round runs without the native Stop backstop until Rex can carry an agent session id; the lead keeps `supervision-watch` in the foreground.
- A worker started by hand, outside `agent start`, lacks the identity and reads as standalone.
- A new Herdr-only behavior must either be emulated by the adapter or refuse under `TEAMLEAD_BACKEND=rex`.
