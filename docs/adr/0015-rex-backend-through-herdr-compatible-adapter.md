# Rex Backend Through a Herdr-Compatible Adapter

## Status

proposed

## Date

2026-11-07

## Context

The team-lead skills drive worker agents through Herdr. The operator's daily terminal is Rex, which has sessions, blocks, labels, a `rex` CLI with JSON output, and per-terminal program status (OSC 7501). A live probe on Rex 0.1.0 (1853) found that Claude Code, Codex and Grok report their own state over OSC 7501, and OpenCode reports none. Rex carries no agent session id. The probe record is in PR #81 (https://github.com/gAmUssA/coding-policy/pull/81).

Herdr is reached from `teamlead/herdr.py` and from seven shell scripts and hooks that run `$HERDR_BIN` and parse its JSON with `jq`.

Options considered:

- **A Python `RexClient` beside `HerdrClient`.** Covers the Python core only.
- **Fork the skills.** Two copies of about 17k lines to keep in step.
- **A Herdr-compatible adapter CLI** that `HERDR_BIN` and `TEAMLEAD_HERDR_BIN` point at.

## Decision

Add `skills/herdr-teamlead/rex-herdr.sh`, a `uv run` launcher for `teamlead/rex_herdr.py`. It implements the Herdr `agent`, `pane` and `workspace` commands the plugin calls, in Herdr's JSON envelope and error codes, over the Rex session in `REX_SESSION`. A Herdr pane is a Rex block, an agent name is a block label, and `agent_status` is the agent's own program status, with `error` read as `done` and no record read as `unknown`. Every answer is JSON, pane reads included (`{"result":{"type":"pane_text","text"}}`); the two consumers of pane text, `HerdrClient` and `wait-report.sh`, read that shape when `TEAMLEAD_BACKEND=rex` and Herdr's raw rows otherwise.

## Consequences

- The core and shell scripts run on Rex unchanged except for the two pane-text readers, which switch on `TEAMLEAD_BACKEND`; no rule carries a Rex exception.
- The adapter is pinned to the Herdr grammar and JSON shapes the plugin reads today. A core change that calls a new Herdr command must add it to the adapter.
- `agent_session` is never returned, so native-transcript report proof is unavailable on Rex.
- Agents without program status (OpenCode, Pi) read `unknown` and are never dispatched.
- A Rex session no client shows stays at 80×24, so worker TUIs wrap a long `REPORT: <path>` row.
- This record covers transport only. A Rex team mode (the `HERDR_ENV` switch and the lead's pane identity) and workers on remote Rex hosts need their own records.
