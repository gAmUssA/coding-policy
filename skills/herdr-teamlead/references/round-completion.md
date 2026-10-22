# Round Completion Reference

Execution detail for Steps 18 and 20 of `skills/herdr-teamlead/SKILL.md`.

## Step 18 — Release the Pull Request

The release is one more assignment, never a prompt into the developer's
existing context. Return to Step 5 with the role `release` for
the developer's agent (template `skills/herdr-teamlead/templates/brief-release.md`, the same
`WORKTREE` and `BRANCH`, a fresh `REPORT`), run Step 6 (it reports
`already-provisioned`), dispatch through Step 8 so the context is cleared and
the brief is fresh, and wait on the report in Step 9. A source-changing
release finding returns to Step 10 for the next counted developer assignment.
A bot disagreement inside this step returns to Step 11 first.
The worker merges after all gates pass. Proceed immediately to Step 19 only
after verifying its reported release against the live VCS and release gates.
Record that evidence in the task ledger.

## Step 20 — Log the Round

Finalize the task ledger with the round outcome and remaining obligations.
Mark the task completed only after its acceptance criteria and required
release and cleanup obligations are verified. Preserve the ledger for resume
and standup. Preserve retrospective notes and link them from the ledger. Save
current progress through the attention owner and stow the lead's handoff under
`skills/herdr-teamlead/references/working-memory.md`. Reconcile supervision
before ending the turn. Report outstanding attention first, followed by the
outcome and saved paths.
