# Judge Round Reference

Execution detail for Steps 11–15 and 17 of `skills/herdr-teamlead/SKILL.md`. Read it
only when Step 10 or Step 18 names a judge trigger. Modes, triggers and both
report contracts are in `skills/herdr-teamlead/references/round-flow.md`
"The Judge".

## Step 11 — Compose the Judge Brief

For a dispute, compose from `skills/herdr-teamlead/templates/brief-judge.md` through Step 5: the
dispute, both positions with report paths, the governing rule, the tree.
Copy each position's cited evidence into `POSITION_A_EVIDENCE` and
`POSITION_B_EVIDENCE`, or "none cited"; never supply citations of your own.
`INVESTIGATION_REPORT` is the investigator's report after an `insufficient`
ruling, otherwise "none".

For an exhausted allowance, compose from `skills/herdr-teamlead/templates/brief-judge-diagnosis.md`
through Step 5 under the role key `judge-diagnosis`, which writes
`brief-judge-diagnosis.md`: the assessed investigator report, the task, rounds
spent, remaining blocking work, the per-round history, the tree, and any prior
remedy with what it changed.
The pinned seat is still `judge`, so plan and dispatch that role and pass this
file as its brief: `--brief judge=<outdir>/brief-judge-diagnosis.md`.

Skip Step 6 for the read-only judge. Proceed immediately to Step 12.

## Step 12 — Re-measure the Shared Window

Run the skill's Step 12 `measure` alone, not the preflight. Add the round's
`--config <config-file>` when it is not the default. Emits and saves the
snapshot under Step 2's headroom contract; exit 1 names `failed_agents`.
Resolve any unreadable judge window before planning. Pass the fresh snapshot to
Step 13; never reuse the earlier reading as affordability proof. Proceed
immediately to Step 13.

## Step 13 — Plan the Judge Seat

Plan the pinned judge against Step 12's fresh snapshot. Use the installed `plan`
invocation in `skills/herdr-teamlead/references/round-setup.md` Step 3 with this
argument synopsis:

```text
plan [--config <config-file>] \
  --roles judge --judge-mode <adjudication|diagnosis> \
  --snapshot <step-12-measure-output> --task <task-id>
```

`adjudication` for a dispute, `diagnosis` for an exhausted allowance — the same
choice Step 11 made when it composed the brief. Use the recorded mode in Steps
14 and 15. An undeclared mode is refused.

Exit 0 names the judge worker; proceed immediately to Step 14. On non-zero,
report the diagnostic and finish here. Never substitute a judge, lower its tier,
or hand-write an assignment to bypass the refusal.

## Step 14 — Start the Judge Worker on Its Pinned Tier

For an existing judge worker, proceed to Step 15 with a clearing dispatch.
For an empty shell pane, complete retrospective checks for the start and run
the skill's Step 14 `start-judge-worker.sh` command.

It starts the pinned judge and verifies launch argv, on the mode Step 13
recorded in the plan. Pass the config Step 13 planned with; a judge tier, agent
kind or launch options that differ from that file are refused. The script header
owns the contract.

- **Exit 0** — proceed immediately to Step 15 with `--no-clear`.
- **Any non-zero** — report the diagnostic and finish here without briefing
  the worker or overriding its tier.

## Step 15 — Dispatch the Judge

Use Step 13's plan under Step 8's dispatch contract. Use the installed `apply`
invocation in the skill's Step 8 with this argument synopsis:

```text
apply [--config <config-file>] \
  --assignments <plan-file> \
  --brief judge=<round>-judge.md --report judge=<absolute-report-path> \
  --common <path-to-COMMON.md> --judge-mode <adjudication|diagnosis> \
  --task <task-id> [--no-clear]
```

Pass the same `--judge-mode` Step 13 planned. Step 8's outcomes govern. Use
`--no-clear` only for the worker just started in
Step 14; an existing judge receives the default cleared relaunch with retrospective
coverage. Apply verifies the live tier before input. Proceed immediately to Step 16.

## Step 17 — Act on the Ruling or Remedy

Apply the Ruling Outcomes contract in `skills/herdr-teamlead/references/round-flow.md`. Investigation
rulings return to Step 10's knowledge gate. Implementation rulings route
unchanged-branch rulings to verified release or renewed verification,
branch-changing rulings to the counted correction path, an `insufficient`
ruling to an investigator and then back to Step 11, and a blocked ruling
to its saved operator question. Only the operator overrides a ruling.
Continue immediately to the step named by that outcome.
