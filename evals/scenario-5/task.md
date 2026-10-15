# Prepare the Merge Script for a Tessl Plugin PR

## Background

Your team uses an automated policy reviewer (`github-actions[bot]`) that runs on every PR push and posts its verdict as a GitHub review. Because of a GitHub API limitation, this bot cannot post an `APPROVE` — so when it re-reviews after you fix its findings, the new verdict lands as a `COMMENT` rather than superseding the original `CHANGES_REQUESTED`. This means the old blocking review stays on the PR and will keep GitHub from merging even though the bot's latest feedback is clean.

The PR described in `inputs/pr-state.json` has gone through exactly this situation: the policy reviewer flagged issues, you fixed them, and the bot's re-review confirmed everything looks good. Now the PR is in `"result": "ready"` state — CI is green, the reviewer's latest pass has no blocking findings — but the stale `CHANGES_REQUESTED` from the first cycle is still there. The repo also publishes to the Tessl plugin registry on merge, so you need a registry snapshot before you merge so post-merge verification has a baseline to compare against.

## Your Task

Write a shell script called `merge-plan.sh` that captures the complete, ordered sequence of commands needed to merge this PR and verify the publication. The script should be ready to run (or serve as the exact reference for what to run) — so use real command forms with the actual values from `inputs/pr-state.json` substituted in.

The script should cover:

1. Any pre-merge housekeeping that must happen before the merge itself
2. The merge command and local branch cleanup
3. Post-merge steps: finding the publish workflow run, watching it, and verifying the publication landed and cleared moderation

Use the helper scripts in `skills/release/` where applicable (they exist in the skill's working environment). For any `gh run watch` calls, write the command as it should appear in practice.

Produce only `merge-plan.sh` — a single executable shell script with the commands in the correct order and real values filled in from `inputs/pr-state.json`.
