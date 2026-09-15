# Round Flow

The shape of one task round, and what the lead does between the steps of
`skills/herdr-teamlead/SKILL.md`. Read this when a round deviates from the happy path.

## Compose the Active Team

| Responsibility | Repository writes | Output |
| --- | --- | --- |
| developer | authorized source work in its own worktree | pushed branch and report |
| tester | test code as a report-directory patch | test plan, patch or verification report |
| reviewer | none | independent COMMENT review and report |
| advisor, investigator, architect | none | bounded recommendation, diagnosis or design report |
| release | release operations only | verified release report |
| judge | none | binding dispute ruling |

Activate the responsibilities the next task decision needs. Add specialty
requirements through `references/specialists.md`; a profile on the bench needs
no worker or monitoring loop. One worker holds one assignment in a dispatch.
Compatible expertise may share a responsibility. Consultations with the same
canonical responsibility use separate dispatches, each with its own engagement.

The owner planner applies capability and contribution eligibility before
affordable task familiarity and measured headroom. Step 5 in the skill names
the executable contract. Use measured costs to improve calibration; preserve
the pinned judge and the developer's early-fix reservation.

If no eligible worker fits, record the gap and staff or sequence the work.
Never silently drop a required responsibility or let a contributor independently
verify its own work. Specialist advice does not replace required reviewer and
tester passes.

## Two Phases

An implementation task runs through the round twice, and only the second pass
gates release. Investigation-only tasks use Step 12's knowledge-deliverable
gate; they do not require a pushed branch or release reports.

**Phase 1 — pre-development.** An architect or advisor supplies the
needed design or interaction report. An investigator may first resolve a causal
question. Assess each consultation before using its outcome. The tester maps each acceptance
criterion to a test, or delivers those tests as a patch (tester Mode A or B).
Plan that work with `--round tester=test_plan` and record its actual contribution
through `assess-specialist` before selecting Phase 2 verifiers. A test patch
author is an implementation contributor, even while holding the tester role.
The developer implements against both, runs the repo's gates, pushes the
branch, and stops without opening a PR.

Work that trips a Team Composition trigger gates here: its deliverable lands
before implementation, or the lead records the staffing decision and its
reason. Step 5's `detect-triggers` run decides which of the four fired, and
reads that recorded decision; the lead's own reading of the diff does not. The cheap gate is the one worth making mandatory; a reviewer catching
the same thing one finding per round is the expensive one.

Untriggered work keeps the old judgement, whatever its size: skip Phase 1 when
a design note would say less than the diff. Nothing in Phase 1 is a pass; it
is preparation.

**Phase 2 — post-push verification (mandatory).** The reviewer reviews the
pushed branch and posts a COMMENT review (Mode B). The tester runs the gates
and the acceptance tests against that same branch (Mode C). Both report against
the current tip by SHA.

The release hand-off reads Phase 2 reports and nothing else. A design note is
not a review of the code that got written, and a test plan is not a test run.

Phase 2 excludes actual design and implementation contributors from reviewer
and tester. The owner applies recorded contribution history; use `--exclude`
for relevant contributions outside it. Assess uncertain prior consultations
under `references/specialists.md`. The reviewer responsibility is verification
only; it no longer carries pre-development Mode A. Historical reviewer
responsibility remains unknown until its actual contribution is established.

Consultations can enter later when a new question could change implementation
or verification. Scope any resulting correction through the accepted behavior
and existing correction allowance; a specialist recommendation grants no new
implementation authority. A completed consultation returns to the next needed
assignment, or closes an investigation-only knowledge deliverable through Step 12.

## One Round, End to End

1. **Roster** — `roster.sh` names the live workers. An unnamed pane has no
   dispatch handle; name it first.
2. **Authority** — `verify-authority.sh <owner/repo>` answers whether the
   operator owns the repo. Ownership is the namespace, never write permission,
   and the answer becomes the authority line in every brief.
3. **Measure** — `teamlead.sh measure` reads each worker's own usage numbers.
   A worker that is `working` or `blocked` is skipped with null windows rather
   than interrupted.
4. **Plan** — `teamlead.sh plan` assigns the requested responsibilities with
   specialty requirements and explicit contribution exclusions where needed.
   It contacts nobody; owner state migration may save an older ledger.
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
   then sends the assignment prompt. The recovery reference governs developer
   fix retention and the separate assessed specialist continuation path.
   It re-reads live status and refuses to type into a busy worker.
9. **Observe** — `supervision-watch` observes every enrolled worker. Verify
   candidates with `wait-report.sh --once`, ledger outcomes, and acknowledge
   handled events under `references/supervision.md`.
10. **Gate** — the lead reads every report in full and decides: another round,
   or the release hand-off.

Before relying on consultation output, save the report delivery receipt and run
`assess-specialist` under `references/specialists.md`. Record the accepted outcome
in the task ledger and resolve its supervision obligations separately. Keep
useful sessions available for likely follow-up, while preserving scoped lessons
outside the session. No idle specialist counts as active work.

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
- **`wait-report.sh --once` exit 1 with `reason: checkpoint_pending`** —
  delivery remains pending. Record the checkpoint, acknowledge its event with a
  scheduled pending recheck, and resume the fleet watcher. Never send a second
  copy of the brief on a status hint.
- **`wait-report.sh --once` exit 1 carrying a `stall` object** — the report is
  absent, the worker is terminal, and the budget measured from `--since` is
  spent. This is terminal for that wait: schedule no further recheck. Read the
  `stall.class`, record the user-attention obligation, and take the recovery
  `rules/agent-team-operation.md` Stalled Workers names for that class. Never
  commit the partial work on the strength of the tree building.
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

Under a recorded `stop` remedy, 2 and 3 read against what ships: the excluded
defect is a tracked accepted defect and the shipped scope carries no other
blocking finding. `rules/review-severity.md` Judge-Accepted Defect Carve-Out
carries the preconditions; every other gate here is unchanged.

A Phase 1 design note or test plan does not satisfy 2 or 3. A report against an
older SHA does not either: re-run Phase 2 against the current tip. After scoped
re-checks close the findings, re-run Phase 2 with `full` briefs before handing off
to release. Scoped reports alone never satisfy this gate.

Reassess both passes against current contribution history before releasing. A
later discovery that a verifier shaped the task's design or implementation
invalidates its independence, even when the reviewed SHA is unchanged. Preserve
the historical receipt, append the new gate decision and its evidence to the
task ledger, and obtain a fresh independent report. Never rewrite the prior
review or treat an old approval as authority over newer contribution evidence.

## Ancestor-Sensitive Fixtures

Some tools resolve their configuration by walking filesystem ancestors, so a
fixture placed under the reports directory is not isolated from the operator's
own project: an isolated `HOME` and the XDG directories are not enough when the
lookup starts at the fixture and climbs. One real rehearsal initialized the
operator's home-level Tessl project that way, and an unauthenticated run then
wrote a rule index missing a private dependency.

The brief names a task-owned fixture root for that work, outside every ancestor
that configures the tool, and the worker keeps every report, plan and patch
artifact under the reports directory as usual
(`rules/agent-team-operation.md` Writers and Checkouts carries the
preconditions).

The root is this assignment's own: created under a name no other assignment
uses, never a directory that already exists and never one reached through a
symlink. The cleanup at the end removes it, and a reused or linked root would
make that cleanup delete somebody else's files.

Inside that root, before any command that writes through the tool:

1. Resolve the root to its physical path, then walk its ancestors and record
   which of them configure the tool.
2. Pre-seed the intended local manifest so the lookup settles inside the
   fixture rather than above it.
3. Prove the effective root the tool resolved, and compare it to the fixture.
   A root anywhere else stops the rehearsal.
4. Record the state of each user-level file the rehearsal can reach, and read
   it again afterwards. An unexpected change stops the rehearsal and is
   reported; restore from that record rather than reinstalling the operator's
   environment.

Which files those are, and which manifest a given tool reads, belong to the
tool's own documentation — not to this reference or a brief.

## Blocking Gate

At Step 12, apply `skills/herdr-teamlead/references/assignment-reasoning.md` to
the findings and their proposed corrections. Preserve required judge rulings
and operator decisions; scope classification never waives a blocking finding.
Read this task's confirmed fix history, name the next fix number,
and return to Step 4 with self-contained briefs carrying the findings and prior
reports. Preserve the developer for retained fixes; use a fresh context for the
fresh-worker stage. Never reset the counter during re-planning. At a contested
verdict or a lead override, go to Step 13 first. At an exhausted allowance,
record the checkpoint through the owner commands in
`skills/herdr-teamlead/references/dispatch-recovery.md`, report implementation
as `awaiting_diagnosis`, consult the investigator, and go to Step 13 with its
assessed report. Use the plan its
remedy records for the bounded extra attempts; collect each preceding attempt's
actual blocking review before continuing.

## Branch-Changing Ruling

At Step 19, the lead never edits the branch itself. At an exhausted allowance,
record the checkpoint through the owner commands in
`skills/herdr-teamlead/references/dispatch-recovery.md` and take the diagnosis;
the boundary is a diagnostic question, not a budget prompt. The plan its remedy
records covers multiple attempts within those bounds; fresh release handoffs do
not ask for context-change permission. Report implementation as
`awaiting_diagnosis` while the diagnosis is pending. Record the remedy once and
continue within it.
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

A Phase 2 review is delivered in the reviewer's report file; there is no PR
yet to post it on. When a PR already exists (a fix round after release
started), the workers share one GitHub account, and GitHub refuses `APPROVE`
and `REQUEST_CHANGES` on that account's own PR, so the review is also posted
as a COMMENT review with each finding labelled blocking or advisory per
`rules/review-severity.md`. The COMMENT state carries no gate, so the LEAD is
the gate: a blocking finding in an internal review sends the round back,
whatever GitHub's merge box says.

## The Judge

A reserved seat outside ordinary staffing, on the most capable model
available. It holds no other responsibility. `rules/agent-team-operation.md`
Judge Seat is the contract; this section is the operational detail for
Steps 13–19 of `skills/herdr-teamlead/SKILL.md`.

It runs in two modes. Adjudication settles a dispute; diagnosis asks why a fix
loop is not converging. Dispatch adjudication on exactly one of three triggers:

- A contested reviewer or tester verdict — one worker's finding, another
  worker's (or the lead's) disagreement, neither side able to settle it by
  re-reading the rule.
- A lead override of a blocking finding — the lead about to waive a finding a
  worker labelled blocking gets a second, independent read first.
- A bot finding the team disagrees with — the policy reviewer or Copilot flags
  something the developer and reviewer both think is wrong.

All three are disputes, and a dispute is settled once. Dispatching an
adjudication at every allowance boundary made the seat a per-round toll on the
window its developer and reviewers already share; that trigger is gone and
stays gone.

An exhausted allowance is a different question, and it gets the second mode —
after the investigator. That profile is written for "unclear causality or
repeated unsuccessful fixes", it is read-only and bounded, and it is the
cheaper seat: it produces the reproduction and causal assessment, and the
judge rules on them. A diagnosis without one is refused.
The loop that exhausts its budget is rarely short of attempts: a find-rate
that holds flat while every round closes its finding is a structural problem,
and more rounds reproduce it. Diagnosis asks why the loop is not converging
and what has to change. The lead ran the loop and is the wrong diagnostician
of its own dispatch pattern, so the read is independent for the same reason a
review is.

Its report opens with six lines rather than three:

```
DIAGNOSIS: <why the loop is not converging, from the evidence>
REMEDY: continue — <rounds, approach unchanged> | restructure — <the change> | stop — <what ships, what is tracked>
BOUND: <developer attempts this remedy allows> — <why that number> | none — for stop
ASSESSMENT: <the investigator report this diagnosis ruled on>
EVIDENCE: <the rounds, findings and diffs the diagnosis rests on>
UNVERIFIED: <anything unconfirmed against the tree, or "none">
```

A `continue` or `restructure` remedy carries the attempt budget in `BOUND`,
which is the number the operator used to supply. It is counted in developer
attempts, justified against the cited evidence, and capped: the recording
command refuses a bound above its ceiling rather than honouring it. `ASSESSMENT`
names the investigator report the judge ruled on, and the record binds that
path the way supervision's enrollment binds the judge's own report. A `stop`
remedy ships what is clean and records the remainder as a tracked accepted
defect; that authority is the judge's, stated so a lead does not re-escalate
out of caution. Recording it also files a user-attention obligation, so the
operator learns of the override they hold without going to look for it.

The diagnosis is re-enterable when its own remedy's bound exhausts with
blocking work remaining. A remedy that was independently diagnosed and still
did not work is evidence for the next diagnosis, not for the operator, who
holds nothing on the second pass they did not hold on the first. Re-entry
moves down the ladder `continue` → `restructure` → `stop`, or repeats one rung
once against a recorded `PROGRESS` line: a remedy that produced nothing is
never reissued, the ladder never runs backwards, a rung already repeated is
spent, and `stop` is terminal, so a task takes at most five diagnoses. No
operator sits in the path of any of them.

`rules/agent-team-operation.md` Judge Seat carries the contract; the record
shapes are the owner's, in `references/dispatch-recovery.md`.

It is read-only without exception in either mode: no file edit, no mutating
git or `gh` command, no GitHub post, no subagent dispatch.

Adjudicating, it reads both positions and the governing rule, verifies the
disputed facts against the tree itself rather than trusting either side's
framing, and returns a report opening with three
lines — `RULING: uphold A | uphold B | amend — <line> | blocked — <question>`,
`ACTION:` naming the minimal step, `UNVERIFIED:` naming anything it could not
check — followed by its numbered reasons.

Those three lines belong to adjudication; a diagnosis carries the five above
and never a `RULING:` or an `ACTION:`.

`blocked` is the judge declining to rule on a dispute it cannot settle from
the tree and the rule text alone. The round stops there and the named question
goes to the operator. The lead does not dispatch a second judge and does not
rule in its place.

The ruling or remedy binds the round the moment the lead reads it. Only the
operator overrides one; record the override and why in the round log. No
diagnosis remedy waits on an operator for the task to reach a terminal state.
A `blocked` adjudication is the one ruling that does: it stops the round and
sends its named question to the operator, as it always has.

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
- **`REMEDY: continue`** — record the diagnosis, then return to Step 12 and
  spend the bounded rounds with the approach unchanged.
- **`REMEDY: restructure`** — record the diagnosis, apply the named structural
  change to the shape of the work, then return to Step 12 within its bound.
  The change is the judge's to name and the lead's to carry out.
- **`REMEDY: stop`** — record the diagnosis, release what is clean, and record
  the remainder as a tracked accepted defect. The remedy carries that
  authority; do not re-escalate it. Proceed to Step 20 for what ships.
- A remedy's bound exhausting with blocking work remaining returns to Step 13
  for the next diagnosis, one rung down the ladder — or at the same rung once,
  when the spent remedy made progress the new diagnosis records in `PROGRESS`.
  Never re-enter above the last rung, and never repeat a rung twice.
