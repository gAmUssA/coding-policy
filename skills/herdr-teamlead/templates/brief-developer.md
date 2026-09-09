# Brief — Developer

Your role this round is **developer**. Read the team protocol in full before
this file.

## Task

Complete `{{ISSUE}}` within the task authorization in COMMON.md.

## Setup

Your worktree already exists at `{{WORKTREE}}`, on branch `{{BRANCH}}`, cut
from the fresh remote default. The lead created it. You do not.

1. Confirm where you are before anything else:

   ```bash
   cd {{WORKTREE}} && pwd && git status -sb
   ```

2. Work only inside `{{WORKTREE}}`. Prefix every command with
   `cd {{WORKTREE}} &&`.
3. Run no git command against `{{SHARED_CHECKOUT}}` — not `worktree add`, not
   `fetch`, not a read. It is another agent's checkout. Everything you need,
   including `git fetch origin`, works from inside your own worktree.

## Prepare the Assignment

Read, in this order:

1. The issue `{{ISSUE}}` and every comment on it.
2. The consumers of the code you are about to change — who calls it, what they
   expect.
3. The architect's design note, posted as a comment on `{{ISSUE}}`.
4. The tester's plan in `{{REPORTS_DIR}}`.

If the design note and the tester's plan disagree, follow the design note and
record the conflict in your report.

For a bug fix, reproduce the user's failing path and compare it with a working
path. Separate the trigger, the conditions that expose or hide it, and the
visible symptom. Name a check that could disprove the proposed cause; run the
smallest feasible experiment and preserve contradictory results. Report any
reproduction or causal evidence gap. Turn the reproduction into a regression
test when implementation is authorized.

For an investigation-only task, apply those diagnostic questions and deliver
the findings, supporting evidence, and uncertainties in your report. Perform
only the investigation actions the brief authorizes. Finish with the report;
the implementation and push stages below apply only to authorized code changes.

## Implement

- Smallest change that satisfies the issue. No drive-by refactors, no
  reformatting of code you did not need to touch.
- Tests ship with the code, in the same commit series — deterministic, asserting
  outcomes.
- One logical change per commit. Imperative subject line under 72 characters, a
  body saying why.
- Run every gate `CONTRIBUTING.md` names, from inside the worktree. Green
  before you push.

## Push, Then Stop

```bash
cd {{WORKTREE}} && git push -u origin {{BRANCH}}
```

**Do not open the PR.** A pushed branch does not trigger the policy reviewer,
which is the point: the tester and the reviewer run against your pushed branch
first, and you fold their blocking findings in before any bot sees the diff.

The lead will send you a follow-up round to open the PR and run the release
skill. Until then your branch is finished work waiting for internal review.

## Report

Write `{{REPORT}}` covering:

- What you implemented and why, decision by decision.
- The branch name and every commit SHA.
- Gate output — the command you ran and its summary line.
- Anything you chose not to do, and why.
- Open questions for the reviewer.
- For a bug, the reproduction, working comparison, causal explanation,
  counterfactual result, and remaining uncertainty.

Final chat message ends with exactly:

```
REPORT: {{REPORT}}
```
