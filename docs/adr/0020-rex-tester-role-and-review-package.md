# Rex Tester Role and Review Package

## Status

accepted

## Date

2026-11-28

## Context

ADR 0019 slice 1. Herdr preserves developer, reviewer and tester responsibilities, and hands reviewers and testers a review package bound to the recorded base and the candidate. Rex had a reviewer reading a detached checkout and no tester.

Options considered:

- **A new `tester` record type.** More schema, a migration, and nothing a role name does not already carry.
- **A role-name convention.** Any read-only worker is already a role; `test` and `test-<n>` name a tester, as `review` and `review-<n>` name a reviewer.
- **Reuse Herdr's `review-package.sh`.** ADR 0016 forbids code shared with the Herdr skills, which are removed in 2.0.0.

## Decision

- A role named `test` or `test-<n>` is a tester. It spawns read-only from an agent kind other than the developer's, like a reviewer.
- `verdict` reads a tester's report for `reviewed: <candidate>` and one `verdict: pass` or `verdict: fail`; a reviewer keeps `approve` or `changes-requested`. Each refuses the other's words with `no_verdict`.
- New `team package --task --candidate` writes `review-<base7>..<candidate7>.diff` (commits, stat, diff) under the task's reports directory. A rebuild with identical content succeeds; a different file of that name is refused with `package_conflict`.
- The skill gains Steps 14–18 (package, spawn, dispatch, wait, gate) before the close. A tester `fail` re-enters the fix loop; reviewer and tester loops share the three-loop cap.

## Consequences

- No `schema_version` change: a tester is an ordinary worker record.
- Easier: independent verification beyond the lead's checks, bound to the exact candidate and package.
- Harder: the lead sequence is five steps longer; the tester and reviewer run serially.
- Later slices may add scoped re-checks and acceptance-test patches (Herdr Modes A and B); this slice covers verification only.
