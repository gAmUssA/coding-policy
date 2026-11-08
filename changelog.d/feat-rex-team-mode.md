---
bump: minor
---
### Added

- **Rex team mode: `skills/herdr-teamlead/rex-team.sh`** (ADR 0016). Started from a Rex terminal, it runs the lead with the environment Herdr gives a managed pane (`HERDR_ENV=1`, `HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`, `HERDR_BIN`, `TEAMLEAD_HERDR_BIN`) plus `TEAMLEAD_BACKEND=rex`, and the Rex adapter's `agent start` now starts each worker behind the same variables for its own block. Every `HERDR_ENV` mode check, hook and rule therefore works in Rex unchanged, while `REX_SESSION` alone, set in every Rex terminal, never marks a round. Covered by `tests/test_rex_team.sh` and `tests/test_rex_team_env.py`.

### Changed

- **`rules/agent-team-operation.md` Two Modes names the Rex entry point** and states that `REX_SESSION` alone never marks a team round.
- **A Rex team round keeps the lead's native Stop backstop.** `supervision-bind` under `TEAMLEAD_BACKEND=rex` binds a Claude Code lead to the session id Claude Code exports (`CLAUDE_CODE_SESSION_ID`), once the adapter confirms Claude Code is the lead pane's agent. The binding record moves to version 2 with a truthful `source: claude-code:env`. Version-1 Herdr bindings are unchanged and still read, so the bump is additive dual-accept. The Stop hook accepts a version-2 binding only under `TEAMLEAD_BACKEND=rex`. A Codex lead, which exports no session id, is refused with the reason and runs without the backstop. Tested both ways through `hooks/herdr-supervision-stop.sh` in `tests/test_supervision.py`, and through `HerdrClient` over the adapter in `tests/test_rex_herdr_core.py`.
- **`label-workspaces.sh` reads `TEAMLEAD_BACKEND` like `teamlead.herdr.default_backend`.** It refuses any value other than `herdr` or `rex`, and under `rex` it refuses before any rename: one Rex session holds the team and a block label is the agent's name, so labelling would overwrite worker names. Tested in `tests/test_label_workspaces.sh`.
