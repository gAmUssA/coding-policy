# Rex Backend Through a Herdr-Compatible Adapter

## Status

proposed

## Date

2026-11-07

## Context

The team-lead skills drive worker agents through Herdr. The operator also runs Rex (superlogical.com/rex), a terminal multiplexer that is their daily terminal, and wants the same team rounds there. Rex has a session, window and block model, labels, a `rex` CLI with JSON output, Lua scripting through `rex do`, and per-terminal program status (OSC 7501).

A live probe on Rex 0.1.0 (1853) on 2026-11-07 established the facts this decision rests on:

- Claude Code, Codex and Grok report their own state over OSC 7501, as `app` `claude-code`, `codex` and `grok-build`. Each went `working` to `done` on a one-step task, with no screen scraping. Grok also reports its trust dialog as `blocked` with `kind=permission`; Claude Code and Codex report nothing before their first prompt.
- OpenCode reports nothing. Pi was not installed on the probe machine.
- A `block.event` paste followed by an `enter` key delivered a multi-line prompt to all three reporting agents, and each wrote its report and printed `REPORT: <path>`.
- Rex records no agent session id, so Herdr's `agent_session` has no counterpart.

The Herdr surface is used in two places: `teamlead/herdr.py` (`HerdrClient`, about 15 commands) and seven shell scripts and hooks that run `$HERDR_BIN …` and parse its JSON with `jq`.

Options considered:

- **A Python `RexClient` beside `HerdrClient`.** Covers the Python core only; the shell scripts and hooks stay Herdr-bound or need a second port.
- **Fork the skills.** Two copies of about 17k lines to keep in step.
- **A Herdr-compatible adapter CLI.** One program implements the subset of Herdr's command grammar the plugin calls, answers in Herdr's JSON envelope and error codes, and drives Rex underneath. `HERDR_BIN` and `TEAMLEAD_HERDR_BIN` point at it.

## Decision

Add `skills/herdr-teamlead/rex-herdr.sh`, a launcher for `teamlead/rex_herdr.py`, implementing `agent list|get|read|prompt|send-keys|wait|start|rename`, `pane get|read|wait-output|send-text|send-keys|process-info|rename`, and `workspace list|rename` over the `rex` CLI. The core and the shell scripts stay unchanged.

The mapping:

- A Herdr pane is a Rex block, its id the full `block:<uuid>`. A Herdr workspace is the Rex session named by `REX_SESSION`; the adapter only sees blocks in that session.
- An agent name is the block's label. A labelled block hosts an agent only while a known agent is its foreground process or its program status names a known `app`; otherwise the name reads as `agent_not_found`, as Herdr releases a name when its agent exits.
- An agent's kind comes from the program status `app`, then from the foreground process name.
- `agent_status` is the root program status record's `state`, with `error` read as `done`. No record reads as `unknown`, which no readiness check accepts.
- `agent_session` is never returned. Report verification falls back to the report file and the `REPORT: ` marker, the delivery signal `skills/herdr-teamlead/references/herdr.md` Why Reports Are Files already names.
- Errors use Herdr's stderr envelope `{"error":{"code","message"}}` with exit 1, and exit 2 for a usage error. The codes the core branches on are kept: `agent_not_found`, `agent_name_taken`, `pane_not_found`, `timeout`.

## Consequences

- Rex rounds reuse every guard, parser, ledger and test in the existing skills; only the adapter is new code.
- The adapter is pinned to the Herdr command grammar and JSON shapes the plugin reads today. A core change that calls a new Herdr command must add it to the adapter or fail under Rex with a usage error.
- Agents that do not report program status (OpenCode, Pi) read `unknown` and are never dispatched until a later change adds a screen-marker state source for them.
- Native-transcript report proof (`report_delivery.probe`) is unavailable under Rex until Rex can carry an agent session id.
- A Rex terminal takes its size from the clients showing it; a session no client shows stays at 80×24. Workers belong in a session the operator has open, or the agent TUIs wrap a long `REPORT: <path>` row and `wait-report.sh` refuses the wrapped marker, as it does under Herdr.
- Workers on another machine's Rex server (`rex hosts`), such as Pi or OpenCode on local models, are out of scope; the adapter talks to one server and one session.
- Herdr already lists `agy` (Antigravity) among its kinds; adding a kind to the adapter is one `APP_KINDS` and one `KIND_BINARIES` entry once that agent ships a terminal CLI.
- Telling a Rex team round from standalone work, the `rex-teamlead` entry point, and the hooks' worker detection are follow-up work: `REX_SESSION` is set in every Rex terminal, so it cannot serve as the mode switch the way `HERDR_ENV` does.
