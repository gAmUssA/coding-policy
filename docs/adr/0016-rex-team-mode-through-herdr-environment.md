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

Emulate the environment. `skills/herdr-teamlead/rex-team.sh` requires `REX_SESSION` and `REX_BLOCK` and prints one JSON object holding `HERDR_ENV=1`, `HERDR_PANE_ID=$REX_BLOCK`, `HERDR_WORKSPACE_ID=$REX_SESSION`, `HERDR_BIN` and `TEAMLEAD_HERDR_BIN` (the adapter) and `TEAMLEAD_BACKEND=rex`. It execs nothing and changes nothing; the operator starts the lead with that environment through a jq `@sh` one-liner in `skills/herdr-teamlead/references/herdr.md`, so the script keeps the plugin's JSON-producing script contract without an exception. The adapter's `agent start` types each worker's command behind `env` with the same variables for that worker's block. `TEAMLEAD_BACKEND=rex` is the only Rex-specific marker, read where Herdr behavior cannot be emulated:

- `supervision-bind` binds a Claude Code lead to the session id Claude Code exports as `CLAUDE_CODE_SESSION_ID`, after the adapter confirms Claude Code is the lead pane's agent. Rex records no agent session, and that id is the one the native Stop hook receives. Every binding record moves to version 2 and names what proved it: `claude-code:env` here, `herdr:<kind>` for Herdr's native proof, `operator:record` for an explicit record. The owner upgrades a version-1 record to `herdr:unrecorded` and rewrites it. The Stop hook never migrates, and it blocks a `claude-code:env` binding outside `TEAMLEAD_BACKEND=rex`.
- `apply` refuses to dispatch unless its caller is the lead the owner state binds with `claude-code:env`, for this pane and this exported session. Herdr's unbound legacy dispatch does not exist in Rex, and a Codex lead, which exports no session id, cannot run a Rex team round.
- `label-workspaces.sh` refuses, since one session holds the team and block labels are agent names.

## Consequences

- A Rex round reuses every hook, rule and script unchanged; the operator opts in by starting the lead with the environment `rex-team.sh` prints.
- Starting the lead is a documented one-liner, not a launcher: it needs `jq`, and a failed `rex-team.sh` starts nothing.
- Standalone agents in Rex stay standalone: `REX_SESSION` alone never marks a round.
- A Claude Code lead in a Rex round has the native Stop backstop: the hook blocks that exact session, and no other, while assignments are active.
- Codex leads are not supported on Rex: `supervision-bind` and `apply` refuse with the reason and the way to start a Claude Code lead. A Codex lead still runs Herdr rounds.
- Herdr bindings now record `herdr:<kind>`. A version-1 record is upgraded by the owner on the lead's next supervision command. Until then the Stop hook treats it as no usable prior state and lets the stop proceed with a warning: blocking on a record it may not migrate would trap the lead in a stop loop. An installed reader older than this change refuses a version-2 record as unsupported.
- A worker started by hand, outside `agent start`, lacks the identity and reads as standalone.
- A new Herdr-only behavior must either be emulated by the adapter or refuse under `TEAMLEAD_BACKEND=rex`.
