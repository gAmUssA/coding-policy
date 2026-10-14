# Version on Merge from Changelog Fragments

## Status

accepted

## Date

2026-10-14

## Context

The publish workflow published the manifest version as-is on every push to `main`. Every PR that changed shipped content therefore bumped `.tessl-plugin/plugin.json` and `.codex-plugin/plugin.json` and wrote a `## <version> — <date>` CHANGELOG heading, and `scripts/check-version-bump.sh` required the bump to be ahead of the registry. Two open PRs both claimed the next version and both edited the top of `CHANGELOG.md`. Whichever merged second had to rebase and bump again. Herdr team rounds produce several PRs at once, so they queued behind one another.

Options considered:

- **Per-PR bump (status quo).** Simple and explicit, but PRs touching shipped content serialize, and every rebase re-runs CI and review.
- **Release-train PRs.** PRs carry no version; a periodic release PR bumps and publishes the accumulated changes. This removes the conflicts but adds a recurring manual PR and delays every change until the train leaves.
- **Fragments with assign-on-merge.** Each PR adds its own `changelog.d/<name>.md` with a requested semver level. The publish workflow computes the version on merge, writes it and the CHANGELOG entry, and commits back. Fragment files never collide, and every merge still publishes promptly.

## Decision

Use changelog fragments with the version assigned on merge.

- A PR changing shipped content adds a fragment with `bump: patch|minor|major` and `### <Section>` entries. It never edits a manifest `version` or adds a version heading. `scripts/check-changelog-fragment.sh` gates this on every PR.
- On push to `main`, `.github/workflows/publish.yml` checks out the current tip of `main` and runs `scripts/assemble-release.sh`. The next version is the highest of the registry's latest and both manifests, bumped by the highest pending level. The script writes both manifests and the CHANGELOG entry and deletes the fragments.
- `scripts/push-release.sh` commits the result as `chore(release): <version>` under `github-actions[bot]` and pushes to `main`, rebasing once if a merge landed meanwhile. The push uses the workflow's `GITHUB_TOKEN`, which starts no new workflow run.
- The workflow publishes only when the manifest version is ahead of the registry.
- `rules/ci-safety.md` Protected Branches carries the narrow exception for this release commit.

## Consequences

- PRs touching shipped content merge in any order without rebasing for the version. Several fragments merged before a publish run go out together as one version at the highest requested level.
- The publish job holds `contents: write` and pushes to `main`. A branch protection rule or ruleset that blocks the workflow's token would break publishing; adding one requires allowing that actor to push first.
- A merge with no pending fragment assigns no version and is not a failure. It publishes only when an already-assigned version never reached the registry. The release skill reads the run's `Publish` step and reports a skipped one as no publication.
- Recovery is re-running the publish workflow. If the push failed, the fragments are still on `main` and are re-assembled from the new tip. If the publish step failed, the version is already committed and still ahead of the registry, so the re-run publishes it.
- A version can be skipped. When a committed version never reached the registry and new fragments arrive first, the next assembly bumps past it.
- When a merge lands between the job's checkout and its push, the rebase keeps both commits. The published artifact then includes that merge's content, while its fragment is assembled into the following version.
- The `publish-plugin` concurrency group keeps one run waiting. When a third merge arrives, GitHub cancels the older waiting run, and the release skill's check for that merge reports a cancelled run. The newest run still assembles every pending fragment from the tip.
- Later work must honor this contract: never hand-edit a manifest `version` or add a version heading in a PR; keep the fragment grammar in `scripts/check-changelog-fragment.sh` as the one definition the assembler sources; and keep the publish run's `headSha` bound to the merge commit, which `skills/release/resolve-publish-run.sh` relies on.
