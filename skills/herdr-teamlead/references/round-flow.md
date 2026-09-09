# Round Flow

The shape of one task round, and what the lead does between the steps of
`skills/herdr-teamlead/SKILL.md`. Read this when a round deviates from the happy path.

## The Three Roles

| Role | Seat weight | Writes code | Output |
| ---- | ----------- | ----------- | ------ |
| developer | heaviest | yes, in its own worktree | pushed branch + report |
| tester | middle | test code only, delivered as a patch file | plan or patch + report |
| reviewer / architect | lightest | never | design note or COMMENT review + report |

Roles rotate between tasks. The rotation is decided by measured headroom
through `teamlead.sh plan`, never by the lead's impression of who looks fresh.
`plan` breaks a headroom tie on who has held the role fewest times, so nobody
owns `developer` forever.

Step 5 in `skills/herdr-teamlead/SKILL.md` identifies the source for the
default weights and assignment algorithm. Re-weigh from measurements: run
`measure` before and after a round and read what the seat actually cost.

Fewer live workers than roles is an error, not a silent drop — a role nobody
holds is work nobody is doing. Either name another agent into the roster or
fold two roles onto one worker deliberately, in that worker's brief.

## Two Phases

An implementation task runs through the round twice, and only the second pass
gates release. Investigation-only tasks use Step 12's knowledge-deliverable
gate; they do not require a pushed branch or release reports.

**Phase 1 — pre-development (optional).** The architect posts a design note on
the issue (reviewer Mode A). The tester writes a plan mapping each acceptance
criterion to a test, or delivers those tests as a patch (tester Mode A or B).
The developer implements against both, runs the repo's gates, pushes the
branch, and stops without opening a PR.

Skip Phase 1 for a change small enough that a design note would say less than
the diff. Nothing in Phase 1 is a pass; it is preparation.

**Phase 2 — post-push verification (mandatory).** The reviewer reviews the
pushed branch and posts a COMMENT review (Mode B). The tester runs the gates
and the acceptance tests against that same branch (Mode C). Both report against
the current tip by SHA.

The release hand-off reads Phase 2 reports and nothing else. A design note is
not a review of the code that got written, and a test plan is not a test run.

Phase 2 plans with the author barred from the seats that judge its work:
`plan --exclude reviewer=<author> --exclude tester=<author>`. The author keeps
whatever seat is left to it — a bar that would strand another role is refused,
naming the role and the exclusions, rather than quietly seating somebody to
review their own branch.

## One Round, End to End

1. **Roster** — `roster.sh` names the live workers. An unnamed pane has no
   dispatch handle; name it first.
2. **Authority** — `verify-authority.sh <owner/repo>` answers whether the
   operator owns the repo. Ownership is the namespace, never write permission,
   and the answer becomes the authority line in every brief.
3. **Measure** — `teamlead.sh measure` reads each worker's own usage numbers.
   A worker that is `working` or `blocked` is skipped with null windows rather
   than interrupted.
4. **Plan** — `teamlead.sh plan --roles developer,tester,reviewer` assigns the
   roles, with `--exclude <role>=<agent>` for every seat a worker must not
   hold. It contacts nobody and writes nothing.
5. **Package** — Step 6 builds a range-specific VCS artifact for reviewer and
   tester briefs. Keep the original task base for full reviews and the prior
   reviewed tip for scoped re-checks; a new tip gets a new package.
6. **Compose** — `compose-briefs.sh` renders the templates from one values
   file, refusing to write anything when a placeholder is unfilled or a
   supplied key matches no template. The lead decides the values; the script
   decides nothing.
7. **Provision** — `provision-worktree.sh` creates every worktree the briefs
   name, from the shared checkout. A worker never runs `git` there, so its
   checkout has to exist before the brief arrives.
8. **Dispatch** — `teamlead.sh apply` uses Step 10's explicit context mode,
   then sends the assignment prompt. Only a same-role fix may retain context.
   It re-reads live status and refuses to type into a busy worker.
9. **Observe** — `supervision-watch` observes every enrolled worker. Verify
   candidates with `wait-report.sh --once`, ledger outcomes, and acknowledge
   handled events under `references/supervision.md`.
10. **Gate** — the lead reads every report in full and decides: another round,
   or the release hand-off.

The lead appends decisions throughout this flow to the persistent task ledger,
including before pauses and handoffs. `references/task-ledger.md` separates
dispatch and report observations from assignment acceptance and task completion.
All references to the round log here mean that ledger.

## Reading a Report

A report is the worker's only channel to the lead. Read all of it, every time —
a `## BLOCKED` section can sit under a report that otherwise reads as finished.

- **Blocking findings present** — take Step 12's bounded fix path under
  `rules/agent-team-operation.md` Fix Loops. Keep a stable task identifier and
  advance its fix counter; changing worker or scope never restarts it.
  Name the findings and prior report in each brief. Re-check the findings
  with scoped briefs, then run full verification before release.
- **A `## BLOCKED` section** — the worker stopped on something it could not
  decide. Resolve it in the NEXT brief, which reaches it through a fresh
  dispatch. Never type the answer into the worker that is waiting.
- **`wait-report.sh` exit 3** — the worker is at an approval or question
  dialog. Read the pane, relay the dialog text to the operator verbatim, and
  stop the round for that worker. The lead never answers it — an approval
  dialog is input, and input to a blocked agent is exactly what Dispatch
  Safety forbids. The operator answers; the wait resumes once
  `herdr agent get <name>` reports a state other than `blocked`.
- **`wait-report.sh --once` exit 1** — delivery remains pending. Record the
  checkpoint, acknowledge its event with a scheduled pending recheck, and resume
  the fleet watcher. Never send a second copy of the brief on a status hint.
- Persist blocked dialogs, missing reports, and required operator decisions in
  the attention queue before presenting them. Keep observing unrelated work.
  A pause or handoff must cover the entire active fleet under the supervision
  reference; a single worker's blocker does not release those obligations.

## Release Gate

For an authorized implementation release, Step 12 requires all four:

1. The developer's report names the branch and the commit SHA it pushed.
2. A broad reviewer **Mode B** report reviews that same SHA and carries no
   blocking finding.
3. A broad tester **Mode C** report verifies that same SHA, with the repo's
   gates run and every acceptance criterion met.
4. Nothing has been pushed to the branch after those two reports.

A Phase 1 design note or test plan does not satisfy 2 or 3. A report against an
older SHA does not either: re-run Phase 2 against the current tip. After scoped
re-checks close the findings, re-run Phase 2 with `full` briefs before handing off
to release. Scoped reports alone never satisfy this gate.

## Blocking Gate

At Step 12, apply `skills/herdr-teamlead/references/assignment-reasoning.md` to
the findings and their proposed corrections. Preserve required judge rulings
and operator decisions; scope classification never waives a blocking finding.
Read this task's confirmed fix history, name the next fix number,
and return to Step 4 with self-contained briefs carrying the findings and prior
reports. Preserve the developer for retained fixes; use a fresh context for the
fresh-worker stage. Never reset the counter during re-planning. At an exhausted
allowance, a contested verdict, or a lead override, go to Step 13 first. Use the
recorded bounded plan for authorized extra attempts; collect each preceding
attempt's actual blocking review before continuing.

## Branch-Changing Ruling

At Step 19, the lead never edits the branch itself. At an exhausted allowance,
record the checkpoint and concrete correction proposal through the owner commands
in `skills/herdr-teamlead/references/dispatch-recovery.md`. Report implementation
as `waiting_for_operator` while the bounded decision is pending; finish the skill
until it arrives. Record an explicit approval once and continue within it.
Otherwise return to Step 12 carrying `ACTION:` verbatim as required work. Count
that implementation as the next fix, under the same task identifier, and gate the
resulting tip again before release.

## Why Phase 2 Runs Before the PR

The registry build's PR #27 went through **12 automated review rounds** —
policy reviewer plus Copilot — because the code reached the bots before the
team's own reviewer and tester had seen it. Each round cost a full CI cycle and
a context reload on the developer.

A plain branch push does not trigger the policy reviewer. That gap is the
opening: the developer pushes the branch and stops. The tester and the reviewer
run against the **pushed branch**, the developer folds their blocking findings
in, and only then does the PR open — so the bots review work the team has
already agreed on, and the usual outcome is one round instead of twelve.

The hand-off is therefore split around the bots:

- Developer runs `Skill(skill: "release")` **Steps 1–4** — readiness, PR,
  version reasoning, and the review request.
- The bots review once.
- Developer runs **Steps 5–7** — watch the reviews, act on blocking findings,
  merge and clean up.

The lead releases nothing until it holds a reviewer Mode B report and a tester
Mode C report against the SHA the developer pushed. A newer push invalidates
both: re-run Phase 2 against the new tip.

## Shared-Account Reviews

The workers share one GitHub account, and GitHub refuses `APPROVE` and
`REQUEST_CHANGES` on that account's own PR. Internal reviews are posted as
COMMENT reviews with each finding labelled blocking or advisory per
`rules/review-severity.md`. The COMMENT state carries no gate, so the LEAD is
the gate: a blocking finding in an internal review sends the round back,
whatever GitHub's merge box says.

## The Judge

A fifth seat, outside the three-role rotation, on the most capable model
available. It never holds developer, reviewer, or tester. `rules/agent-team-operation.md`
Judge Seat is the contract; this section is the operational detail for
Steps 13–19 of `skills/herdr-teamlead/SKILL.md`.

Dispatch it on exactly one of four triggers:

- A contested reviewer or tester verdict — one worker's finding, another
  worker's (or the lead's) disagreement, neither side able to settle it by
  re-reading the rule.
- A lead override of a blocking finding — the lead about to waive a finding a
  worker labelled blocking gets a second, independent read first.
- A fix loop exhausted its allowance with blocking work remaining — the judge
  rules before another correction proposal. Step 19 records the checkpoint
  and waits for an explicit bounded plan. An approved plan covers multiple
  attempts within its recorded bounds; fresh release handoffs do not ask for
  context-change permission. See `references/dispatch-recovery.md`.
- A bot finding the team disagrees with — the policy reviewer or Copilot flags
  something the developer and reviewer both think is wrong.

It is read-only without exception: no file edit, no mutating git or `gh`
command, no GitHub post, no subagent dispatch. It reads both positions and the
governing rule, verifies the disputed facts against the tree itself rather
than trusting either side's framing, and returns a report opening with three
lines — `RULING: uphold A | uphold B | amend — <line> | blocked — <question>`,
`ACTION:` naming the minimal step, `UNVERIFIED:` naming anything it could not
check — followed by its numbered reasons.

`blocked` is the judge declining to rule on a dispute it cannot settle from
the tree and the rule text alone. The round stops there and the named question
goes to the operator. The lead does not dispatch a second judge and does not
rule in its place.

The ruling binds the round the moment the lead reads it. Only the operator
overrides one; record the override and why in the round log.

The judge worker is declared in the main `config.json` and is measured and
planned like every other seat, but its seat is pinned rather than ranked: the
`judge` block names the agent, model and effort, the planner never chooses who
judges, and the pinned worker never holds another seat.

It runs on the same Claude subscription as the `claude` worker, so both
declare the same `window_group` and the planner charges a seat's cost against
every worker sharing that window. When that window is exhausted the round
halts: there is no substitute judge, no fallback to another vendor's flagship,
and no degraded ruling.

## What the Lead Never Does

- Edit the shared checkout. The lead reads it and dispatches; workers write.
- Answer a question by typing into a working worker. Wait for the report.
- Answer a blocked worker's approval dialog. Relay it to the operator and stop.
- Create a worktree for a worker after dispatch. Provision before briefing.
- Write an authority line by hand. It comes from `verify-authority.sh`.
- Brief a write action on a repo the operator does not own without their
  explicit per-repo, per-action permission recorded in the brief.
- Release on a Phase 1 report. A plan is not a verification.
- Treat Herdr status as assignment acceptance or task completion.
- Merge on a worker's behalf. The developer runs the release skill.
- Act against a judge's ruling, or seat the judge on developer, reviewer, or
  tester. Only the operator overrides a ruling.

## Dispatch Results

Step 10 of `skills/herdr-teamlead/SKILL.md` follows these outcomes. Before a
finish, apply the whole-fleet pause/handoff contract in `references/supervision.md`.

- **Exit 0** — proceed to Step 11.
- **Busy target** — no dispatch occurred. Wait for readiness or replan; stay
  at this step.
- **Sent but not started** — inspect the pane; never re-dispatch on top of the
  message. Proceed to Step 11 for the roles that started.
- **Retrospective, clear, composer, tier, qualification, or continuity refusal** — follow the
  diagnostic and recorded dispatch outcome. Reconcile uncertainty before retrying;
  wait for roles whose records confirm dispatch.
- **Unknown refusal** — report it verbatim and finish here.
- **`--dry-run`** — inspect the context choice, requested tier, and relaunch
  argv. It contacts no worker, writes no ledger, and proves no live tier or
  qualification. Finish here.


## Ruling Outcomes

Step 19 follows these branches. Before a finish, preserve any user question and
apply the whole-fleet pause/handoff contract in `references/supervision.md`.

The `RULING:` line binds the round. Only the operator overrides it.

For an investigation-only task, a non-blocked ruling returns to Step 12's
knowledge-deliverable gate with the ruling and any required authorized research.
Apply the existing correction allowance and judge rules to remaining findings.
Do not enter implementation Phase 2 or Step 20 without implementation/release
authorization. A blocked ruling follows the operator-question path below.

- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing no branch content**
  — record the ruling. Proceed to Step 20 only with Step 12's broad reports
  against the current tip. Otherwise re-run Phase 2 with full briefs carrying
  the ruling. Do not re-dispatch the judge for the same settled dispute.
- **`uphold A` / `uphold B` / `amend`, `ACTION:` changing the branch** — apply
  the round-flow reference's Branch-Changing Ruling contract. Finish here while
  its required operator decision is pending; otherwise return to Step 12 with
  `ACTION:` as the next counted fix.
- **`blocked`** — the judge declined to rule. Stop the round and put its
  named question to the operator. Do not dispatch a second judge and do not
  rule in its place. Finish here.
