# Rex Team Lead Herdr Parity Roadmap

## Status

proposed

## Date

2026-11-28

## Context

ADR 0016 shipped a lean `rex-teamlead`: a developer, an independent reviewer, lead-run checks and a three-loop fix cap. It left every other Herdr capability to later slices, each with its own decision. Herdr is deprecated for removal in coding-policy 2.0.0, so the Rex lead must cover what the Herdr lead does before that removal.

The operator asked for full parity, excluding what exists only because Herdr reads panes (screen probes, `REPORT:` pane markers, pane labelling, the `rex-herdr.sh` adapter).

Gap, by Herdr contract section (`skills/herdr-teamlead/references/team-operation.md`):

| Capability | Herdr | Rex today |
| --- | --- | --- |
| Roles | developer, reviewer, tester, specialists, judge | developer, reviewer |
| Review package bound to base and head | `review-package.sh` | reviewer reads a checkout at the candidate SHA |
| Fix loops | typed rounds, correction plans, retained context | one correction brief, three-loop cap |
| Stalled and no-work recovery | `no_work_recovery`, `recovery`, Stalled Workers | `timeout` is rerun by hand |
| Specialist consultations | on-demand | none |
| Judge seat | pinned judge, `REMEDY: stop` | none |
| Round tiers and model usage | `tiers`, `measure`, ADR 0013 | `--model` and `--effort` flags only |
| Authority and policy | `verify-authority.sh` | none |
| Task ledger | round evidence | none |
| Working memory | stow and resume of lead handoffs | none |
| User attention | catch-up pages | none |
| Fleet supervision | enrollment, Stop hook | none (ADR 0016: no Stop backstop) |
| Retrospectives | saved notes, curated lessons | none |
| Standup | `herdr-standup` | none |

Options considered:

- **Port Herdr's capabilities in one change.** One review, but a reviewer cannot hold nine capabilities and their schema bumps in one diff.
- **Nine slices in dependency order.** Each slice is reviewable and revertible on its own.
- **Stop at the lean lead.** Keeps Rex small but leaves Herdr-only capabilities unreachable once Herdr is removed.

## Decision

Close the gap in the slices below, one PR and one ADR each, in dependency order. Every slice keeps ADR 0016's principles: scripts own deterministic transitions, one JSON store owned by `rexlead/team.py`, results bound to exact inputs, no pane parsing, no code shared with the Herdr skills.

1. **Tester role and review package.** Generalize roles beyond `dev` and `review`; add a tester brief and a package bound to base and candidate.
2. **Fix-loop depth and stall recovery.** Typed fix rounds, a no-work and stall diagnosis from Rex status, a bounded recovery.
3. **Authority and policy.** Verify repo authority before briefs, per `rules/external-repo-contributions.md`.
4. **Specialists and judge seat.** On-demand consultations; a pinned judge whose `stop` ruling feeds the Judge-Accepted Defect Carve-Out in `rules/review-severity.md`.
5. **Task ledger.** Round evidence owned by the lead.
6. **Working memory and user attention.** Stow, resume and catch-up on top of the ledger.
7. **Tiers and usage.** Model tiers per role with a measured shared window.
8. **Supervision.** Enrollment and a Stop backstop bound to the lead's own Rex block and session.
9. **Retrospectives and standup.**

## Consequences

- Easier: each capability ships, reviews and reverts alone; the gap table gives a checklist for judging parity.
- Harder: nine PRs with review rounds each, and `skills/rex-teamlead/SKILL.md` grows with every slice.
- Each slice ships a `schema_version` bump of the task record when it adds fields, with the owner migration `rules/stateful-artifacts.md` requires.
- `rules/agent-team-operation.md` and `skills/rex-teamlead/SKILL.md` grow per slice; the Rex lead stays a step-by-step skill rather than a contract read on demand.
- Parity is reached when slice 9 merges. Slices 1 and 2 are prerequisites for 4; slice 5 is a prerequisite for 6 and 9.
- This ADR does not set the Herdr removal date. The skills' "removed in coding-policy 2.0.0" notice stands until the operator decides otherwise; slices not yet merged by then stay Herdr-only gaps.
