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

- `supervision-bind` binds a Claude Code lead to the session id Claude Code exports as `CLAUDE_CODE_SESSION_ID`, after the adapter confirms Claude Code is the lead pane's agent. Rex records no agent session, and that id is the one the native Stop hook receives. The binding record moves to version 2 and names its source, `claude-code:env`, never Herdr's `herdr:<kind>`. Only a round with `TEAMLEAD_BACKEND=rex` writes or accepts that source, and the Stop hook blocks a version-2 binding outside one.
- `label-workspaces.sh` refuses, since one session holds the team and block labels are agent names.

## Consequences

- A Rex round reuses every hook, rule and script unchanged; the operator opts in by starting the lead through `rex-team.sh`.
- Standalone agents in Rex stay standalone: `REX_SESSION` alone never marks a round.
- A Claude Code lead in a Rex round has the native Stop backstop: the hook blocks that exact session, and no other, while assignments are active.
- A Codex lead exports no session id, so its `supervision-bind` is refused. The owner state stays unbound, dispatch runs without the backstop, and the lead keeps `supervision-watch` in the foreground.
- Binding records are dual-version: version 1 (Herdr proof, no `source`) and version 2 (exported identity with `source`) are both read, and neither is migrated. An installed reader older than this change refuses a version-2 binding as unsupported, which can only happen in a Rex round started by this version.
- A worker started by hand, outside `agent start`, lacks the identity and reads as standalone.
- A new Herdr-only behavior must either be emulated by the adapter or refuse under `TEAMLEAD_BACKEND=rex`.
