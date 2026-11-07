---
bump: minor
---
### Added

- **Rex team mode: `skills/herdr-teamlead/rex-team.sh`** (ADR 0016). Started from a Rex terminal, it runs the lead with the environment Herdr gives a managed pane (`HERDR_ENV=1`, `HERDR_PANE_ID`, `HERDR_WORKSPACE_ID`, `HERDR_BIN`, `TEAMLEAD_HERDR_BIN`) plus `TEAMLEAD_BACKEND=rex`, and the Rex adapter's `agent start` now starts each worker behind the same variables for its own block. Every `HERDR_ENV` mode check, hook and rule therefore works in Rex unchanged, while `REX_SESSION` alone, set in every Rex terminal, never marks a round. Covered by `tests/test_rex_team.sh` and `tests/test_rex_team_env.py`.

### Changed

- **`rules/agent-team-operation.md` Two Modes names the Rex entry point** and states that `REX_SESSION` alone never marks a team round.
- **`supervision-bind` refuses under `TEAMLEAD_BACKEND=rex`** with an actionable message instead of a Herdr-specific one: Rex records no agent session id, so the owner state stays unbound and dispatch runs without the native Stop backstop. Tested in `tests/test_supervision.py`.
- **`label-workspaces.sh` refuses under `TEAMLEAD_BACKEND=rex`** before any rename: one Rex session holds the team and a block label is the agent's name, so labelling would overwrite worker names. Tested in `tests/test_label_workspaces.sh`.
