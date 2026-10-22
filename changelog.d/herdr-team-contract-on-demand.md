---
bump: minor
---
### Changed

- **The Herdr lead reads the team contract on demand.** `rules/agent-team-operation.md` no longer has the lead read all of `skills/herdr-teamlead/references/team-operation.md` before each round. The lead now reads the contract's core before Step 1, and reads every other section at the `herdr-teamlead` step that names it. Every section still binds every round. The core (Reading This Contract, Two Modes, a new Lead Seat section split out of Writers and Checkouts, Reports, Authority and Policy, User Attention, Fleet Supervision) now sits at the top of the file. No contract bullet was dropped. Each `herdr-teamlead` step lists its step reference and contract sections in a typed `text` block, which also folds in the PR #67 advisory on inline-code reference paths. The contract read before Step 1 drops from 32,589 to 3,723 bytes. See ADR 0012. Adapted from upstream jbaruch/herdr-policy 64a8082.
- **`test_skill_invocations.sh` guard 7 checks contract-section pointers.** It reads typed `text` block pointers as well as prose "`<path>` Step N" pointers. It refuses a `<path> <Section>` line with no exact `## <Section>` heading, and any other text-block line that is not a reference pointer. It is proven against fixtures in both directions and against a broken pointer in the real `SKILL.md`.
