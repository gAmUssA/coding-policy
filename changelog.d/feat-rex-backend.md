---
bump: minor
---
### Added

- **`skills/herdr-teamlead/rex-herdr.sh`, a Herdr-compatible adapter over Rex, as a transport preview** (ADR 0015). It implements the `agent`, `pane` and `workspace` commands the team-lead skills call, answers in Herdr's JSON envelope and error codes (`agent_not_found`, `agent_name_taken`, `pane_not_found`, `timeout`), and drives the Rex session in `REX_SESSION` through the `rex` CLI, launched with `uv run`. Agent state comes from each agent's own OSC 7501 program status, which a live probe on Rex 0.1.0 (1853) confirmed for Claude Code, Codex and Grok; OpenCode reported none. `process-info` reports the shell as the idle pane's foreground process and the agent's parent as `shell_pid`, the shapes `launch.holds_only_shell` and `restoration._pane_state` read; `agent start` returns only once the agent reports a ready prompt. Rex keeps one label per block and it carries the agent's name, so `pane rename` refuses an agent's block with `pane_title_unsupported` instead of renaming the agent out of every name lookup. Every failure, usage errors and launcher setup errors included, is Herdr's stderr JSON envelope with a next step in its message; the launcher JSON-escapes every control byte and never forwards raw `uv` or interpreter stderr. A Rex team round still needs a team mode, which this change does not add. Covered by `tests/test_rex_herdr.py` (fake `rex` at the process boundary), `tests/test_rex_herdr_core.py` (the unchanged `launch` and `restoration` functions run against the adapter) and `tests/test_rex_herdr_launcher.sh`.

### Changed

- **`TEAMLEAD_BACKEND` selects how pane text is read.** Unset or `herdr` keeps Herdr's raw `agent read`/`pane read` rows; `rex` makes `HerdrClient` and `wait-report.sh` read the adapter's `{"result":{"type":"pane_text","text"}}` and refuse anything else; any other value is refused. The adapter answers JSON for every command, so the plugin's JSON-producing script rule needs no exception.
