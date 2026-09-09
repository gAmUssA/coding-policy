---
alwaysApply: true
---

# CI Safety

## Hands Off CI Config

- **Never smuggle CI configuration changes** (workflow files, pipeline configs) into a PR whose stated scope is something else
- A PR whose title and body explicitly scope the work as a CI change **is** the approval artifact; this rule forbids unannounced edits, not CI-scope PRs
- For unplanned CI edits discovered mid-task, stop and ask before touching the workflow files

## Never Skip Tests

- Never add `[skip ci]` to commit messages
- Never disable or skip failing tests to unblock a merge
- If tests fail, fix the tests or fix the code
- If a test needs an external tool or dependency, install it in CI — "it's hard to install" is not a reason to skip
- Narrow exception for dismissing a gating bot's superseded `CHANGES_REQUESTED`.
- Applies when a gating bot that cannot `APPROVE` (`github-actions[bot]` — GitHub returns HTTP 422) re-reviews clean but its earlier `CHANGES_REQUESTED` keeps the merge `BLOCKED` until dismissed
- Preconditions (all required):
  1. The dismissed review is a `CHANGES_REQUESTED` from a bot on the `GATING_BOTS` allowlist in `skills/release/dismiss-stale-reviews.sh`, never a human reviewer
  2. The same bot posted a later all-clear on the PR — the accepting states are the script's decision predicate, not restated here; `DISMISSED` or `PENDING` is not an all-clear
- Deterministic form is `skills/release/dismiss-stale-reviews.sh`; a hand dismissal meeting both preconditions is equally sanctioned
- Every other dismissal still gates: a bot `CHANGES_REQUESTED` no all-clear superseded, or any human's change request, blocks the merge until resolved through review

## Branch Naming

- Use the convention: `<type>/<description>` (e.g., `feat/add-auth`, `fix/null-pointer`, `chore/update-deps`)
- `<type>-<issue-number>` is an accepted alternative where the repo's existing branches already use it
- Keep branch names lowercase with hyphens
- Flag naming before a PR exists — merged branches are precedent, not violations

## Always Watch CI

- After every push, watch the CI run to completion — never assume it will pass
- If CI fails, inspect the logs immediately, fix the issue, and push again
- A task is not done until CI is green
- Watch the event, not a stopwatch: bind the watch to the terminal signal it awaits — a run's `conclusion`, a review verdict posted — never to an agent-chosen elapsed time
- Poll interval and give-up budget are script-owned constants, never numbers an agent picks per run
- Never wrap a watch in an invented wall-clock `timeout` — a watcher gives up only at its own documented budget
- The pre-merge review watch runs ONLY through `skills/release/watch-pr-reviews.sh` — never a hand-rolled poll loop
- A bot review is complete when its verdict posts (state leaves `none`), zero inline comments included — never wait for comments to appear
- For a reviewer workflow, the run `conclusion` reports only that the workflow finished, never that the review happened — a fail-open gate can report `success` having reviewed nothing
- Gate a reviewer workflow on its posted verdict, not the check's color; never promote it to a required branch-protection gate while a fail-open path exists
- A failed PR check that no event re-triggers stays red until an explicit `gh run rerun --failed` once its cause is fixed
- For plugin/package releases, the duty extends past merge — confirm the resolved run's conclusion and the publication's own published-artifact evidence; no single signal is authoritative
- The duty is keyed on the publication, never on the package — a package that publishes through more than one channel owes it once per publication, each confirmed against the channel that carried it
- Channel-independent, whatever publishes the package:
  1. Resolve the run for that publication, bound to its workflow, its exact commit, the `push` event and the ref that fired it (`skills/release/resolve-publish-run.sh`), never "latest on main"
  2. Watch that resolved run to a terminal state
  3. Require its `conclusion` to be `success`
  4. Verify the version actually published on the channel that carried it
- Two runs matching all four binding facts are an ambiguity to resolve, never a winner to pick — see the `skills/release/resolve-publish-run.sh` header
- Tessl publication (the registry form), all required, in this order:
  1. Before merge: capture the registry's latest version as baseline (`skills/release/capture-registry-baseline.sh`)
  2. Confirm the run's `conclusion` is `success` AND the registry advanced past the baseline (`skills/release/verify-publish-landed.sh`)
  3. Confirm the published version's moderation state cleared (`skills/release/verify-moderation-cleared.sh`) — a still-pending or blocked state at budget exhaustion is an unconfirmed release, surfaced as a failure
- GitHub tag/asset publication: the immutable release exists at the exact tag the run published, is not a draft, and every asset is retrievable (`skills/release/verify-github-release.sh`)
- A Tessl publish confirmed on the registry says nothing about another channel's release, and a green GitHub release says nothing about a pending Tessl moderation — each needs its own evidence
- Never derive an expected version from the merge SHA's manifest and compare against it; never invent a moderation state

## Checks Not Starting

- When pushed checks sit in `queued` and no `github-actions` run is created, check the PR's merge state before assuming an Actions outage
- Diagnose with `gh pr view <N> --json mergeable,mergeStateStatus` — `CONFLICTING` / `DIRTY` is the cause
- Fix: rebase the branch onto the base, resolve conflicts, push — the `github-actions` suite runs and `mergeStateStatus` flips to `UNSTABLE` / `CLEAN`

## Protected Branches

- Don't push directly to `main` or `master`
- All changes go through pull requests
