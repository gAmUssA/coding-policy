---
bump: minor
---
### Fixed

- **A closed no-work initial developer assignment can be retried once through `teamlead recover-no-work`**. `references/team-operation.md` Stalled Workers calls a clean, unchanged stall a `not_sent`-equivalent that may be retried, but the owner refused a second initial assignment and the only way forward spent fix 1. The new owner command takes the saved `wait-report.sh --worktree --base` JSON. It verifies the stall against the dispatch's enrolled worker, report and task base, and requires the stalled checkout to resolve to the one worktree the developer brief's worktree field names. It then rechecks live state: the pane is gone, the enrollment is resolved, the report is absent, and the checkout is clean at the base. It then writes a receipt. `plan` and `apply` admit one fresh initial assignment of the unchanged brief, with a fresh report path and context. `apply` repeats every live check before any input and again immediately before reservation, so a report that lands late refuses the retry, and binds the retry to the receipt in the reservation save. The original row and the correction count are unchanged, and replay grants nothing more. Proven through `teamlead.sh` with a real git checkout and a real `wait-report.sh` run in `skills/herdr-teamlead/tests/test_no_work_recovery.py`. The test failed on the prior code with `invalid choice: 'recover-no-work'` and the initial slot refused. Adapted from upstream jbaruch/coding-policy 45f98ac2 (#763).

### Changed

- **Recovery store version 10 adds `no_work_recoveries`**. The owner migrates a version-9 store by adding the empty collection, and refuses a version-9 store that already carries it. The schema is in `skills/herdr-teamlead/state-schema.md` Recovery records.
