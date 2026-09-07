---
alwaysApply: true
---

# Boy Scout Rule

## The Principle

- Leave the codebase in better shape than you found it. If you see something wrong while doing something else, fix it
- "Pre-existing" is not a valid concept. The state you observed is the state you own, regardless of who or what put it there
- Applies to anything you'd flag if a colleague had just written it: failing tests, broken docs, dead code, stale comments, lying type signatures, leaked secrets, missing newlines, unbumped versions
- Reviewer findings are fair game too — folding a reviewer's advisory in is boy-scouting, subject to the timing rule below

## How to Apply

- **In-scope cleanups** (typo, missing newline, broken doc link, small drift): roll into the current PR. The bundle is fine when the PR's stated scope still reads coherently
- **Out-of-scope discoveries** (untested module, contradicting rules, unrelated security gap): open a follow-up PR — or an issue if the fix needs design — and reference it from the current one. Walking away with no record is the failure mode this rule prevents
- When unsure whether to bundle or split, prefer **bundle small + cite** or **split large + file**. The wrong answer is "leave it"

## Fold Into a Round Already in Flight

- The cost unit is the extra round, not the extra line
- Fold loose ends into work already happening
- Never spin up a fresh round just to boy-scout
- A blocking finding is forcing another push: clean up the adjacent loose ends in that same round
- The change is already mergeable: merge and file the loose ends to the next ticket rather than pushing another commit only to fold them in
- The review-pipeline form is `rules/review-severity.md`

## Reconciliation With `commit-conventions`

- `rules/commit-conventions.md`'s "Keep PRs focused" and this rule appear to conflict. They don't. Focus governs the SHAPE of the bundle: one logical change per commit / PR. Boy-scout governs whether you walk away from problems you noticed: you don't
- A focused PR can include adjacent cleanup commits when the scope reads as one cohesive change. A focused PR cannot include unrelated rewrites — but it CAN include a follow-up reference to where those are tracked
