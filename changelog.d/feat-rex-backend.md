---
bump: minor
---
### Added

- **`skills/herdr-teamlead/rex-herdr.sh`, a Herdr-compatible adapter over Rex** (ADR 0015). It implements the `agent`, `pane` and `workspace` commands the team-lead skills call, answers in Herdr's JSON envelope and error codes (`agent_not_found`, `agent_name_taken`, `pane_not_found`, `timeout`), and drives a Rex session through the `rex` CLI. Pointing `HERDR_BIN` and `TEAMLEAD_HERDR_BIN` at it runs the existing Python core and shell scripts unchanged on Rex. Agent state comes from each agent's own OSC 7501 program status, which a live probe on Rex 0.1.0 (1853) confirmed for Claude Code, Codex and Grok; OpenCode reported none. A smoke run started a Claude worker through `agent start`, prompted it with `agent prompt --wait`, and ran the unchanged `roster.sh` and `wait-report.sh` against the adapter. Covered by `tests/test_rex_herdr.py` (48 cases, fake `rex` at the process boundary) and `tests/test_rex_herdr_launcher.sh`.
