---
bump: minor
---
### Added

- **`rex-teamlead` tester role and review package**. A role named `test` or `test-<n>` is a read-only tester whose report carries `verdict: pass` or `verdict: fail`; `team package` writes the immutable base-to-candidate package the tester reads. The skill gains Steps 14–18 and `templates/brief-tester.md`. See [ADR 0020](docs/adr/0020-rex-tester-role-and-review-package.md), slice 1 of [ADR 0019](docs/adr/0019-rex-teamlead-herdr-parity-roadmap.md). Proven by new tests in `skills/rex-teamlead/tests/test_team.py` that fail with the `team.py` change reverted.
