---
alwaysApply: true
description: Sync the local checkout with the remote default branch before reading, planning, or editing
---

# Sync Before Work

## Sync Before Reading

- At the start of any task that will read or modify a repo, sync the local checkout with the remote default branch
- Run `git fetch origin` before the first read — not after the first failure
- The default branch you start from must match what the remote currently has, not whatever the local clone was left at
- `hooks/check-git-sync.sh` reports the drift at session start; the fetch and sync are still yours to run

## Land on the Fresh Default

- On the default branch: fast-forward to `origin/<default>`
- Local default diverged or stale: rebase or reset onto `origin/<default>` before branching
- Cut the feature branch from the synced default, never from a stale local tip

## Staleness Poisons Conclusions

- Local default behind `origin/<default>` by more than a trivial amount: treat every "this file looks like X" conclusion as suspect until re-derived against the fresh tree
- An issue naming files, skills, or steps absent from the local tree is a staleness tell — fetch and re-derive before mapping the work onto what you see

## Working Against a Pinned Ref

- Narrow exception for starting work from a specific older ref instead of the fresh default.
- Preconditions (all required):
  1. The user explicitly names the older ref or commit to work against
  2. You state you are starting from that pinned ref rather than `origin/<default>`
- Every other task starts from the synced remote default
