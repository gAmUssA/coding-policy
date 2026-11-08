---
bump: minor
---
### Added

- **`skills/rex-teamlead`, a Rex-native team round** (ADR 0016). A lead opens one Rex session per task with `rex-team.sh up`. It then spawns each worker as an interactive Claude Code, Codex or Grok tab in its own worktree under `~/.worktrees/`, marked with `REX_TEAM_TASK` and `REX_TEAM_ROLE`, dispatches a brief with `assign`, and blocks on `wait`. A dispatch is delivered when the worker's own OSC 7501 program status reaches `done` after the dispatch, read from Rex's `program_status_changed` events, and the report file's first line is that dispatch's nonce. No pane text is read, no screen state is guessed and no `REPORT:` line is parsed. The skill covers a developer, an independent reviewer of a different agent kind on a read-only checkout of the exact candidate, and a bounded fix loop before the release skill. A live round on Rex 0.1.0 (1853) on 2026-11-07 had a Claude Code developer commit and a Codex reviewer approve that commit, each delivered on its own `done` event with the matching nonce. Every command can be repeated or resumed: a repeated `spawn` reconnects to a live worker, a failed launch rolls its worktree and branch back, `up` after a Rex restart keeps the task's base and workers, and `down` forgets a worker whose tab was already closed, removing reviewer checkouts and keeping the developer's worktree for the release. The design borrows from jbaruch/software-factory: scripts own transitions, one state store, explicit Rex ids. It stays on interactive workers and Tessl packaging, with no hosted routing service. Covered by `skills/rex-teamlead/tests/test_team.py`: a fake `rex` at the process boundary, a real event pipe and real `git`. The launcher is covered by `tests/test_rex_team_launcher.sh`.

### Changed

- **`rules/agent-team-operation.md` names a Rex team round.** Workers carry `REX_TEAM_TASK`, and `REX_SESSION` alone never marks a round, since Rex sets it in every terminal. A Rex worker's report starts with the brief's `task-nonce:` line instead of ending its chat with `REPORT:`. A Rex lead's contract is the `rex-teamlead` skill. `rules/sync-before-work.md` now says team lead and worker for the sync duties both modes share.

### Deprecated

- **`herdr-teamlead`, `herdr-standup`, the ADR 0015 Rex adapter (`rex-herdr.sh`) and the Herdr hooks** are frozen and slated for removal in coding-policy 2.0.0. They stay as they are until then, and the new skill shares no code with them.
