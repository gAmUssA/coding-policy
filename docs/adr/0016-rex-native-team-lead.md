# Rex-Native Team Lead

## Status

proposed

## Date

2026-11-07

## Context

ADR 0015 ran the Herdr skills on Rex through a Herdr-compatible adapter. Emulating Herdr kept its costs: screen-derived state with flicker probes, `REPORT:` pane markers that wrap, per-agent composer and slash-command delivery, and supervision bindings with versioned migrations that grew review round after review round. Rex reports each agent's own state over OSC 7501 and streams `program_status_changed` events, which removes the reason for most of that machinery.

The operator chose a Rex-native architecture with no Herdr compatibility. A live probe on 2026-11-07 ran one round end to end: a Claude Code developer committed in its own worktree, and a Codex reviewer approved that exact commit from a read-only checkout. Both were awaited on their own program status, and each report was bound to its dispatch by a nonce.

Options considered:

- **Keep the adapter (ADR 0015).** Herdr's model stays the contract.
- **Adopt jbaruch/software-factory.** A Rex-native, headless, application-driven factory. It rests on headless runs where the operator chose interactive workers, depends on the hosted Jev service, ships through ACR rather than Tessl, and is unfinished by its own inventory.
- **A lean Rex-native skill in this plugin**, borrowing software-factory's principles: scripts own deterministic transitions, one state store, explicit Rex ids, results bound to exact inputs.

## Decision

Add `skills/rex-teamlead`, with no code shared with the Herdr skills:

- **One session per task.** Each worker runs as an interactive agent TUI in its own tab and its own worktree under `~/.worktrees/`.
- **Worker markers.** Workers start with `REX_TEAM_TASK` and `REX_TEAM_ROLE` set; `rules/agent-team-operation.md` reads that marker for a Rex team round.
- **State.** A worker's state is its OSC 7501 program status, awaited through Rex events.
- **Delivery.** A dispatch is delivered when the status reaches `done` after the dispatch and the report file's first line is that dispatch's nonce. No pane text is parsed.
- **Storage.** One JSON record per task, at `schema_version` 1, owned by `rexlead/team.py`.

The Herdr skills, the ADR 0015 adapter and their hooks are frozen and deprecated, for removal in coding-policy 2.0.0.

## Consequences

- Completion and delivery no longer depend on pane width, screen layout or agent footers.
- Each agent needs trust in the repository once. Claude Code's trust covers a repository's worktrees; Codex asks per folder and shows update and hook prompts that report no state, which `spawn` surfaces as `not_ready`.
- Workers are interactive: a permission prompt reaches the lead as `blocked` with its `kind`, for the operator to answer in the worker's tab.
- This first slice covers a developer, a reviewer and a bounded fix loop. Testers, specialists, the judge, tiers and usage, the ledger, memory, retrospectives, the standup and a Jev-backed routing judgment are later slices, each with its own decision.
- The lead has no Stop backstop yet. Later work must bind it to the lead's own Rex block and session.
