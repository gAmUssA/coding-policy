# Brief — Release

Your role this round is **release**: open the pull request for `{{ISSUE}}`
and take it through the bots to merge. Read the team protocol in full before
this file.

COMMON's authorized task actions govern this assignment. If they do not cover
the required PR, review, merge, publish, and cleanup actions, report BLOCKED
before any repository or GitHub write. This role grants no additional permission.

## Setup

Your worktree is `{{WORKTREE}}`, on branch `{{BRANCH}}`, already pushed. The
lead created it. You do not.

1. Confirm where you are before anything else:

   ```bash
   cd {{WORKTREE}} && pwd && git status -sb && git log --oneline -1
   ```

2. Work only inside `{{WORKTREE}}`. Prefix every command with
   `cd {{WORKTREE}} &&`.
3. Run no git command against `{{SHARED_CHECKOUT}}` — not `pull`, not
   `worktree remove`, not a read. It is another agent's checkout. The lead
   fast-forwards it and removes your worktree after the merge.

## Before You Open the PR

Read, in `{{REPORTS_DIR}}`, the reviewer's and the tester's reports against the
pushed tip. Both must cover the full branch and satisfy the lead's release
gate. Missing, older, scoped-only, or blocking reports require a `## BLOCKED`
report to the lead; stop this assignment before opening or merging the PR.
List deferred advisories with their existing follow-up references.

## Release

Run `Skill(skill: "release")` from Step 1 through Step 7, in order. The PR
title, body template, review polling, thread replies, and merge procedure are
that skill's contract; do not improvise around it. The PR body's contribution
declaration names the tools that did the work. Never paste a report file into
the PR.

You do not edit repository content in the release role. If any release step,
CI failure, or external review requires a source change, report the current
tip, findings, review URLs, and completed release actions under `## BLOCKED`,
then stop. The lead dispatches the next counted developer fix in a fresh
session and obtains full independent verification before release resumes.
Reuse the task's approved correction bounds; a normal release clear needs no
new context-change permission. Do not reset the count or waive any release gate.

## Report

Write `{{REPORT}}` covering:

- The PR URL and every review round: who posted, the verdict, what changed,
  and the reply on each thread.
- The merge commit SHA.
- Anything you chose not to do, and why.

Final chat message ends with exactly:

```
REPORT: {{REPORT}}
```
