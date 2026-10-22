# Round Setup Reference

Detailed setup contracts, commands and examples for Steps 2–7 of
`skills/herdr-teamlead/SKILL.md`. The skill retains the execution order and
continuation gates; each step's command block lives here.

Step 5's brief-completeness requirements apply to every composed brief: identify
every issue, finding, file, and prior report in full. Phase 2 names the pushed
SHA. Fixes carry the prior attempt count and required ownership handoff.
Verification names `full` or `scoped`, prior findings, and the follow-up for new
advisories. Final release verification is `full`.

## Step 2 — Run the Preflight

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" preflight [--config <config-file>] \
  --repo <repo-path> --slug <owner/repo> \
  [--base <recorded-base> [--head <pushed-head>] [--planned <planned.json>]]
```

A round on a non-default config passes the same `--config <config-file>` to
every `preflight`, `measure`, `plan`, `start-judge-worker.sh` and `apply` call;
the default is `$XDG_CONFIG_HOME/teamlead/config.json`.

Gathers the five pre-planning facts in one run. Each fact reuses its standalone
owner; the sections below document those owners for recovery and for a
standalone rerun.

| Field | Source | Shape |
| ----- | ------ | ----- |
| `roster` | `roster.sh` | `exit`, `caller`, `agents`, `empty`, `diagnostic` |
| `authority` | `verify-authority.sh <owner/repo>` | `exit`, `result` (the script's verdict), `diagnostic` |
| `retrospective` | `retro-check`'s daily check, no transitions | `due`, `cadence`, `error` |
| `headroom` | `measure` over every configured worker | `saved`, `snapshot`, `failed_agents`, `skipped_busy`, `error` |
| `triggers` | `detect-triggers` without roles, requirements or decisions | `declaration`, `base`, `head`, `fired`, `triggers[]` with `signals` |

- `verdict` is `ready` or `stop`. `stops` lists every blocker found in this
  run as `{code, message, reference}`.
- `read_next` lists each stop's reference, plus `specialists.md` when any
  trigger fired. An all-clear run lists nothing.
- `triggers` is `null` with `triggers_reason` when no `--base` is given or a
  declaration or classification stop prevented it. A fired trigger is a fact,
  never a stop; `detect-triggers` with `--roles` stays the staffing check.
- The headroom step is a real `measure`: it sends usage commands to idle
  workers and saves the snapshot to `--state`. It is skipped when the roster is
  empty or unavailable.
  Busy workers are skipped; unmeasured billing stays `unknown`.
- `--head` and `--planned` require `--base`. `--herdr-bin` also points
  `roster.sh` at that binary; `GH_BIN` reaches `verify-authority.sh` from the
  environment. `--now` fixes the check and snapshot time. The measure tuning
  flags match `measure`.
- **Exit 0** — the facts were gathered; a `stop` verdict is an answer.
- **Exit 1** — a JSON error on stderr for a malformed `--slug`, a missing
  `--repo`, `--head` or `--planned` without `--base`, an unreadable config or
  planned file, or a git failure on the given revisions. Nothing was measured.
- **Exit 2** — an argparse error.

| Stop code | Resolve under |
| --------- | ------------- |
| `roster_empty`, `roster_unavailable` | Roster below |
| `authority_not_owned`, `authority_failed` | Authority below |
| `retrospective_due`, `retrospective_unavailable` | `skills/herdr-teamlead/references/retrospectives.md` |
| `headroom_failed`, `headroom_unavailable` | Headroom below |
| `trigger_declaration`, `triggers_unclassified` | `skills/herdr-teamlead/references/specialists.md` |

### Route the Verdict

- **Non-zero exit** — report the diagnostic, fix the named argument or config,
  and rerun.
- **`verdict: "ready"`** — continue with After a Ready Verdict below.
- **`verdict: "stop"`** — read every `read_next` reference, then resolve each
  `stops` entry:
  - `roster_empty`, `roster_unavailable`, `authority_failed` — report the
    message verbatim and finish here.
  - `authority_not_owned` — reuse explicit per-action operator permission;
    absent permission, remain read-only or finish here.
  - `retrospective_due`, `retrospective_unavailable` — complete or reconcile the
    retrospective, then rerun the preflight.
  - `headroom_failed`, `headroom_unavailable` — obtain the missing readings
    before relying on those seats.
  - `trigger_declaration`, `triggers_unclassified` — write the declaration or
    pass `--planned`, then rerun the preflight.

### After a Ready Verdict

Record the `authority` evidence under Authority below. Record task
authorization and permitted actions under the same section. Create or resume
the stable ledger under `skills/herdr-teamlead/references/task-ledger.md`;
record its absolute path before dispatch. Apply the accepted-behavior, resume
and supervision binding requirements at the end of Authority.

Record staffing gaps under Roster below. Leave unused specialist profiles
unlaunched. Never duplicate targets or fold verification onto a contributor.
Start workers in YOLO mode under `skills/herdr-teamlead/references/model-tiers.md`;
preserve it on relaunch. Verify live permission flags before dispatch,
including existing workers. Proceed immediately to Step 3.

### Roster

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Takes no arguments. Emits `{"caller":{"pane_id":...},"agents":[{"name","kind","pane_id","state"}]}`,
listing every named live agent other than your own pane.

- **Exit 0 with a populated `agents`** — the roster is usable.
- **Exit 0 with `agents: []`** — nobody is named. Report the unnamed panes from
  `herdr agent list` and the `herdr agent rename <pane-id> <name>` command that
  fixes it, then finish here (`roster_empty`).
- **Exit 1** — the precondition failed (outside Herdr, or `herdr` absent).
  Report the message verbatim and finish here (`roster_unavailable`).
- **Exit 2** — herdr failed. Report the message verbatim and finish here (`roster_unavailable`).

An unused specialist profile needs no pane. When the next responsibilities
outnumber eligible workers, record a staffing decision: name another configured
worker or schedule separate bounded assignments. Combine compatible expertise
inside one responsibility when justified. Never combine independent verification
with the design or implementation it assesses, and never duplicate dispatch targets.

### Authority

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/verify-authority.sh" <owner/repo>
```

Emits `{"repo","viewer_login","owner_login","owner_type","viewer_permission","namespace_owner","authorized"}`.
`authorized` reflects namespace ownership alone; write permission never sets it
(`rules/external-repo-contributions.md` Default Deny).

- **`authorized: true`** — set `AUTHORITY_STATEMENT` to
  `owner of <owner/repo>` and additional `EXTERNAL_PERMISSION` to `none`.
- **`authorized: false`** — set `AUTHORITY_STATEMENT` to
  `not owner of <owner/repo>`. Record existing explicit operator permission
  naming the repo and each action type in `EXTERNAL_PERMISSION`. If none
  covers the required write, prepare a preview and ask only for that missing
  permission under `rules/external-repo-contributions.md`, or proceed read-only.
  Without permission, set `EXTERNAL_PERMISSION` and `AUTHORIZED_ACTIONS` to
  `none`. Never infer permission from a role assignment (`authority_not_owned`).
- **Exit 1** — a precondition failed: usage, `gh` or `jq` absent, or `gh` not
  logged in. Report the message verbatim, finish here (`authority_failed`).
- **Exit 2** — GitHub could not answer. An unanswerable question is not a
  permission. Report it and finish here (`authority_failed`).

For either result, `TASK_AUTHORIZATION` records the actual operator source and
words. `AUTHORIZED_ACTIONS` names the target repo and the actions permitted for
this round within that task. Use `none` for read-only repository work. Existing
authorization persists; do not ask again for covered actions. Ownership alone
does not authorize release or convert an inspection task into implementation.

Apply `skills/herdr-teamlead/references/assignment-reasoning.md` to the task's
accepted behavior. Bind the lead's live session under
`skills/herdr-teamlead/references/supervision.md` before dispatch. On a resumed
round, read the latest lead handoff and its required files under
`skills/herdr-teamlead/references/working-memory.md`. Reconcile the attention
queue under `skills/herdr-teamlead/references/attention.md`; an unanswered item
survives both a new session and a change of task.

For an owner who requested shipping `example/project`, a release round can
carry `owner of example/project`, the actual shipping instruction in
`TASK_AUTHORIZATION`, and `example/project: create PR, request reviews, reply
to review threads, merge, publish, and clean up the released branch` in
`AUTHORIZED_ACTIONS`. `EXTERNAL_PERMISSION: none` then means no additional
non-owner permission is needed. Include only actions covered by the actual task.

For an inspection-only task, owner or non-owner, `AUTHORIZED_ACTIONS: none`
keeps the repository read-only. An unapproved non-owner assignment also uses
`EXTERNAL_PERMISSION: none`; the lead selects read-only work or stops. A role
requiring writes must report BLOCKED under COMMON's bounds before any write.

### Headroom

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure [--config <config-file>]
```

Sends each configured worker its own usage command and parses the reply. Emits
one snapshot document — per agent: `kind`, `state`, `herdr_state`,
`state_source`, `windows`, `credits`, `plan`, `headroom_pct`, `skipped` — and
appends it to the state file (`skills/herdr-teamlead/state-schema.md`). A worker that
is `working` or `blocked` is reported as skipped with null windows, never
interrupted. A `working` verdict is confirmed against the pane before it counts:
`state_source` names which signal decided, `herdr` or `probe`, and `herdr_state`
carries what herdr claimed. Exit 1 means at least one agent could not be
measured; the snapshot still prints and names it in `failed_agents`.

The usage marker is confirmed in the text that gets parsed, never in a wait
alone. `--marker-poll-attempts` and `--marker-poll-interval` bound the
confirming poll; `--marker-timeout` and `--lines` size the pane read. Attempt
counts and intervals default to the script's own constants; see
`skills/herdr-teamlead/teamlead/measure.py`.

Add `--trace` (or `TEAMLEAD_TRACE=1`) when a live run does something the JSON
does not explain: every herdr invocation, its exit status, and its output go to
stderr, and stdout stays the machine-readable document. Traced fields are
redacted for credential shapes and capped per field with a `[truncated N bytes]`
marker. The tracing contract is in
`skills/herdr-teamlead/references/herdr.md`, "Tracing a Live Run".

Report a `failed_agents` entry to the user and measure that worker by hand
before relying on its role (`headroom_failed`).

## Step 3 — Plan the Roles

Choose the next needed responsibilities before selecting workers. Consult the
profiles and requirement contract in `skills/herdr-teamlead/references/specialists.md`; available
profiles need no activation until a bounded question or deliverable warrants it.
Supply its requirements file for specialized work. Keep the developer reserved
through early fixes; schedule consultation and verification as the task needs them.

Step 2's preflight lists the fired triggers without a staffing check. Run
`detect-triggers` against the task's base, with the roles and requirements
this round intends. It classifies the diff against this repo's
`.herdr/triggers.json` and exits 1 when a fired trigger is neither staffed nor
answered by a recorded staffing decision. A Phase 1 round has no diff yet and
passes `--planned` naming the surfaces the work will touch; a round that
classifies neither a diff nor a plan is refused. Each invocation reads only the inputs
it is given, so re-run it after staffing a role or recording a decision and
plan only once it exits 0. The declaration's fields, the
decisions file and the answers each trigger accepts are in
`skills/herdr-teamlead/references/specialists.md`.

Staff each fired trigger in the roles below or record a decision with its
reason, then confirm with the staffing check. Pass the same `--planned` for a
pre-implementation round:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" detect-triggers \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--planned <planned.json>] [--decisions <decisions.json>]
```

Exit 0 means every fired trigger is staffed or answered. On exit 1, read the
stderr object: an absent declaration is written first (`skills/herdr-teamlead/references/specialists.md`),
and an `unaddressed_trigger` is staffed in the roles below or answered by a
recorded decision with its reason. Re-run the command with the updated
declaration, roles, requirements and decisions after every such change, and
plan only once it exits 0.

A round that will split its review surface validates the partition first:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" validate-partition \
  --repo <repo-path> --base <recorded-base> [--head <pushed-head>] \
  --partition <partition.json>
```

Exit 1 names every unowned path, every overlap and every slice owning nothing,
in one run. Fix the partition and re-run; plan only once it exits 0. The
document's format, the ownership payload and the seating it produces are in
`skills/herdr-teamlead/references/review-partition.md`.

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan [--config <config-file>] \
  --roles <role[,role...]> [--requirements <requirements.json>] \
  [--exclude <role>=<agent>[,<agent>...]]... [--judge-mode adjudication|diagnosis] \
  [--round <role>=<round-type>] [--round-context <evidence.json>] \
  [--snapshot <measure-output>] \
  --task <task-id> [--fix-round <N>] [--correction-plan <id> --work <work.json>]
```

A judge seat declares its mode: `adjudication` rules on a contested verdict,
`diagnosis` on the investigator's assessment at an exhausted allowance. Pass the
same `--judge-mode` to `apply`. `--snapshot` plans against a named `measure`
output instead of the newest saved one; the judge round passes its fresh
re-measurement (`skills/herdr-teamlead/references/judge-round.md`).

Pure computation over the newest snapshot plus the assignment ledger. Contacts
no agent and appends no assignments. Loading older state may perform owner migrations.
Emits the assignment plan with `rationale`, `snapshot_ref`, task context, and
normalized `requirements` when supplied. Qualified tier data accompanies tiered plans.
Exit 1 names the reason it could not plan; resolve the diagnostic before continuing.

`--exclude` bars workers from one responsibility and repeats, once per role.
The owner applies recorded contribution exclusions to task-bound reviewer and
tester assignments. Add explicit exclusions for external contributors and
authorship missing from the ledger; a clear, model switch or new label never
establishes independence. See `skills/herdr-teamlead/references/specialists.md` for assessed contribution
history. Exit 1 covers an unknown role, missing capability or tier, and any field
that cannot fill the requested responsibilities. Resolve the actual diagnostic;
never weaken required independence to fill a seat.

Planning also refuses a field nothing could have been ranked in: a snapshot
missing an agent `config.json` declares, an `--exclude` list naming nobody the
snapshot measured, and a ranked seat measured against a single agent beside the
pinned judge. Re-run `measure` over the whole roster rather than
planning from a single-worker snapshot; the pinned judge seat and a config whose
rankable roster holds one worker are the only exemptions.

For a retained fix, plan `--roles developer` and exclude every other rotating
worker from that role. Use the task's existing developer, not a new headroom
winner. Reserve that developer through initial and early-fix verification before
reusing it for another task or role. Plan the reviewer and tester separately for
post-push verification.

For a follow-up that will use `--retain-specialist`, plan the consultation alone
with its original requirements and worker. Use exclusions to preserve that
worker; a changed candidate requires a fresh handoff. Follow the owner checks in
`skills/herdr-teamlead/references/dispatch-recovery.md` instead of treating a warm pane as dispatch proof.

`--roles` keys the output document. Configured `role_costs` calibrate its costs.
`skills/herdr-teamlead/teamlead/composition.py` owns capability, contribution, familiarity and model-family evidence;
`skills/herdr-teamlead/teamlead/planner.py` owns affordability, fill order and headroom optimization.
The same eligibility checks run before unsent apply. Familiarity reflects a
matching dispatch, never expertise or completed work. Requirements do not change
the reserved judge or the developer correction contract.

Tiered configs select each candidate from its per-agent `tiers` table. A
round choice never overrides a model. Supply the fix number when planning
fixes; pass that same number at dispatch. Register the original task/base with
`teamlead task` and use the same `--task` on plan and apply. Authorized extra
fixes also share `--correction-plan` and `--work`; their owner-managed bounds
are documented in `skills/herdr-teamlead/references/dispatch-recovery.md`. Keep YOLO mode and supported
UI options across worker restarts. The config, round-input, and
billing-evidence contracts are in:

```text
skills/herdr-teamlead/references/model-tiers.md
```

Save the plan and its rationale to a file for dispatch. Relay the `rationale` lines to the user
as the round's role announcement: they name the weight behind each seat, the
exclusions applied, and any worker whose headroom reading is stale. Proceed
immediately to Step 4.

## Step 4 — Build the Review Package

For reviewer or tester briefs, run from a checkout containing the recorded
commits:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/review-package.sh" \
  <recorded-base-sha> <pushed-head-sha> <round-reports-dir>/review-<base7>..<head7>.diff
```

Record the pre-round tip before the first development dispatch. Preserve that
SHA as the task's base through every fix. A full review uses that base and the
current pushed tip; a scoped re-check uses the preceding reviewed tip as its
base. Never infer the base from `HEAD~1`. Set `REVIEW_BASE` and `REVIEW_HEAD`
to those full SHAs. Rebuild for each changed range, including the final full
review after scoped fixes.

For a pre-development tester plan, use the recorded base for both endpoints.
That empty-range package is planning input, never verified implementation.
Consultation, developer-only, release, and judge briefs need no package;
proceed to Step 5. Reviewer briefs are post-push verification only.

Success prints only the absolute artifact path, not JSON. The file contains
the resolved range, commit list, stat, and patch. Set `TEAMLEAD_REPORTS_DIR`
to use the default range-specific filename instead of passing OUTFILE.
Exit 2 names invalid input; exit 1 names a tool or output failure. On either,
fix the named cause and repeat; never compose verification briefs without a
completed package. Existing different content is preserved.

Proceed immediately to Step 5 with the printed path as `REVIEW_PACKAGE`.

## Step 5 — Compose the Briefs

Consult the applicable working-memory lessons and verify any operational fact
the assignment relies on. Include concise relevant lessons with source pointers
in the role's task text; do not copy the whole memory store into every brief.
Apply `skills/herdr-teamlead/references/assignment-reasoning.md` when the task is
a bug or the brief carries review corrections. Name the accepted behavior and
the evidence questions that the worker must answer.

Resolve the policy artifacts before writing the values file:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/resolve-policy-paths.sh" <absolute-shared-checkout>
```

Exit 0 emits `POLICY_INDEX` and `RELEASE_SKILL`; copy both into `shared`.
On non-zero, report the diagnostic and repair the named input before composing.
Lookup precedence and optional global-root input belong to the resolver's
header contract. Never delegate path selection to a worker.

Write the remaining values, then compose:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/compose-briefs.sh" \
  "$CP/skills/herdr-teamlead/templates" \
  <values.json> <round-reports-dir>
```

The values file is `{"shared": {...}, "roles": {"<role>": {...}}}`; a role's
own value beats the shared one. Emits
`{"common":"<path>","briefs":{"<role>":"<path>"}}`. Exit 2 means validation
failed and nothing was written — an absent or unreadable policy artifact or review package,
an unfilled placeholder, a supplied key no template uses, a value that is not
text, a relative, multiline, existing or duplicated `REPORT` destination,
a report overlapping a generated brief, or a `REPORT` longer than the
script's limit (the worker's `REPORT: <path>` line must fit one pane row for
Step 9 to confirm it; use a short reports directory). Exit 3 means the placeholder scan
itself failed, so whether the briefs are clean is unknown: re-run, never
dispatch on it. The placeholder set and both validation directions are the
script's contract; see the header of
`skills/herdr-teamlead/compose-briefs.sh`. On any non-zero exit, fix the
diagnostic before dispatch. Use a fresh absolute report path per role and attempt.

What you decide, and it is the whole of your job here:

- `SHARED_CHECKOUT` — the checkout the workers read.
- `POLICY_INDEX` and `RELEASE_SKILL` — the resolver's absolute artifact paths.
- `AUTHORITY_STATEMENT`, `TASK_AUTHORIZATION`, `AUTHORIZED_ACTIONS`, and
  `EXTERNAL_PERMISSION` — Step 2's verified ownership, actual operator source,
  bounded task actions, and any additional non-owner permission.
- Per role: `ISSUE`, `BRANCH`, `WORKTREE`, `REPORT`, `REPORTS_DIR`, and the
  phase and mode that role runs this round.
- For reviewer and tester: `REVIEW_PACKAGE`, `REVIEW_BASE`, and `REVIEW_HEAD`
  from Step 4. Missing, empty, or non-file package paths refuse composition
  before any brief is written.
- For advisor, investigator or architect: the bounded consultation values in
  `skills/herdr-teamlead/references/specialists.md`; the composer selects `brief-specialist.md`.
- For specialist developer, reviewer or tester work: `SPECIALIST_CONTEXT` in
  the normal role brief, with the applicable expertise, inputs and capability gaps.
- For a judge adjudication: `DISPUTE_KIND`, `QUESTION`, `POSITION_A`,
  `POSITION_A_REPORT`, `POSITION_A_EVIDENCE`, `POSITION_B`,
  `POSITION_B_REPORT`, `POSITION_B_EVIDENCE`, `INVESTIGATION_REPORT`,
  `GOVERNING_RULES`, and `TREE` (see `skills/herdr-teamlead/references/judge-round.md` Step 11).
- For a judge diagnosis under the role key `judge-diagnosis`:
  `INVESTIGATION_REPORT` (the assessed investigator report the judge rules on),
  `TASK`, `FIX_ROUNDS`, `REMAINING_WORK`, `ROUND_HISTORY`, `TREE`, and
  `PRIOR_REMEDY` (any earlier remedy and what it changed, or "none").

| Phase | Role | Mode | Output |
| ----- | ---- | ---- | ------ |
| 1 or a needed consultation | architect, advisor or investigator | consultation | design, advice or diagnostic report |
| 1 | tester | A or B | test plan, or acceptance tests as a patch |
| 1 | developer | — | implementation, pushed branch, no PR |
| 2 | reviewer | B | COMMENT review of the pushed branch |
| 2 | tester | C | gates plus acceptance tests against the pushed branch |
| 3 | release | — | PR opened, bot rounds answered, merged, branch deleted |

Phase 2 briefs name the branch AND the commit SHA the worker must report
against. A report against an older tip does not gate anything.

Name the issue, file, finding, and report path in full in every brief.
Context retention follows Fix Loops or Specialist Consultations in
`skills/herdr-teamlead/references/team-operation.md`. Fresh-worker fix briefs include
the prior attempt count and the ownership handoff that section requires.
Reviewer and tester verification briefs name `full` or `scoped` review,
the prior findings, and the follow-up issue for new advisories. The final
release-gating verification is `full`. Proceed immediately to Step 6.

## Step 6 — Provision the Worktrees

Prune first, every round:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/prune-worktrees.sh" <shared-checkout>
```

Emits the worktrees and branches removed, each kept one with its reason, and
`failed`; exit 2 lists every check or removal git refused. Exit 1 decided
nothing: fix its diagnostic and re-run before provisioning. Report every kept
`dirty`, `unmerged`, `locked` and `detached` entry to the operator; never
remove them by hand.

Then run once per writing worker and every worktree named in a brief:

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/provision-worktree.sh" \
  <shared-checkout> <branch> <worktree-path> [base-ref]
```

Emits `{"path","branch","base_ref","state"}`, where `state` is `created`,
`attached`, or `already-provisioned`. Exit 1 is a precondition (an invalid
branch name, a path outside the worktree root); exit 2 means git refused, or
the path holds something else. Branch-name and path rules are the script's
contract; see the header of
`skills/herdr-teamlead/provision-worktree.sh`.

The lead provisions every worktree a brief names, so a worker never runs git
against the shared checkout (`rules/agent-team-operation.md`
Workers). A consultation inspecting artifacts without git needs none. Provision
a read-only checkout if its evidence work requires git. Remove worktrees per
`rules/agent-worktree-isolation.md` Cleanup once the branch lands.

On any non-zero exit, fix the input it names and re-run this step; do not
dispatch a brief whose worktree does not exist. Proceed immediately to Step 7.

## Step 7 — Label the Layout (optional, once per team)

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/label-workspaces.sh" \
  <lead-label> [<agent>=<workspace-id>]...
```

Names the lead's workspace, each worker's workspace after its agent, and each
worker's pane after its kind. With no pairs, the workspaces come from the
roster. Emits `{"lead":{...},"agents":[...]}` with a per-target
`renamed|unchanged|failed`. Exit 3 means at least one rename failed and the
JSON says which. Report label failures and continue; a label failure never
stops a round.

Run this once per team, not once per round: a name already in place is
reported `unchanged` and nothing is sent. Skip it on a team whose sidebar is
already named. Proceed immediately to Step 8.
