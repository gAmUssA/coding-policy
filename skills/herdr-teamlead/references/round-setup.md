# Round Setup Reference

Detailed setup contracts and examples for Steps 2–9 of `skills/herdr-teamlead/SKILL.md`.
The skill retains the execution order and continuation gates.

Step 7's brief-completeness requirements apply to every composed brief: identify
every issue, finding, file, and prior report in full. Phase 2 names the pushed
SHA. Fixes carry the prior attempt count and required ownership handoff.
Verification names `full` or `scoped`, prior findings, and the follow-up for new
advisories. Final release verification is `full`.

## Step 2 — Verify Herdr and the Roster

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/roster.sh"
```

Takes no arguments. Emits `{"caller":{"pane_id":...},"agents":[{"name","kind","pane_id","state"}]}`,
listing every named live agent other than your own pane.

- **Exit 0 with a populated `agents`** — proceed to Step 3.
- **Exit 0 with `agents: []`** — nobody is named. Report the unnamed panes from
  `herdr agent list` and the `herdr agent rename <pane-id> <name>` command that
  fixes it, then finish here.
- **Exit 1** — the precondition failed (outside Herdr, or `herdr` absent).
  Report the message verbatim and finish here.
- **Exit 2** — herdr failed. Report the message verbatim and finish here.

Fewer named workers than roles is a decision, not a detail: either name another
agent or fold two roles onto one worker in that worker's brief, and say which
you did.

## Step 3 — Verify Authority for the Repo

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
  `none`. Never infer permission from a role assignment.
- **Exit 1** — a precondition failed: usage, `gh` or `jq` absent, or `gh` not
  logged in. Report the message verbatim, finish here.
- **Exit 2** — GitHub could not answer. An unanswerable question is not a
  permission. Report it and finish here.

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
Proceed immediately to Step 4.

## Step 4 — Measure Headroom

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" measure
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
before relying on its role. Proceed immediately to Step 5.

## Step 5 — Plan the Roles

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/teamlead.sh" plan \
  --roles developer,tester,reviewer \
  [--exclude <role>=<agent>[,<agent>...]]... \
  [--round <role>=<round-type>] [--round-context <evidence.json>] [--fix-round <N>]
```

Pure computation over the newest snapshot plus the assignment ledger. Contacts
no agent and appends no assignments. Loading older state may perform owner migrations. Emits `{"assignments":{"<role>":"<agent>"},"rationale":[...],"snapshot_ref":{...}}`.
Exit 1 names the reason it could not plan.

`--exclude` bars agents from one role and repeats, once per role. Phase 2 bars
the author of the branch from `reviewer` and `tester` (`rules/agent-team-operation.md`
Review Before PR). Exit 1 covers an exclusion naming a role outside `--roles`,
and an exclusion set no assignment satisfies.

For a retained fix, plan `--roles developer` and exclude every other rotating
worker from that role. Use the task's existing developer, not a new headroom
winner. Reserve that developer through initial and early-fix verification before
reusing it for another task or role. Plan the reviewer and tester separately for
post-push verification.

`--roles` keys the output document. `role_costs` in config.json re-weighs a
seat per install. The weights, fill order, and tie-breaks are the planner's
contract; see `skills/herdr-teamlead/teamlead/planner.py` — the `plan`
docstring and `DEFAULT_ROLE_COSTS`.

Tiered configs select each candidate from its per-agent `tiers` table. A
round choice never overrides a model. Supply the fix number when planning
fixes; pass that same number at dispatch. Register the original task/base with
`teamlead task` and use the same `--task` on plan and apply. Authorized extra
fixes also share `--correction-plan` and `--work`; their owner-managed bounds
are documented in `references/dispatch-recovery.md`. Keep YOLO mode and supported
UI options across worker restarts. The config, round-input, billing-evidence,
and qualification contracts are in:

```text
skills/herdr-teamlead/references/model-tiers.md
```

Save the output to a file for dispatch. Relay the `rationale` lines to the user
as the round's role announcement: they name the weight behind each seat, the
exclusions applied, and any worker whose headroom reading is stale. Proceed
immediately to Step 6.

Default planning excludes unqualified tiers. Use `plan --preview-tiers` only to inspect an uncommissioned table; live dispatch still requires qualification.

## Step 6 — Build the Review Package

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

Before development, use the recorded base for both endpoints. That empty-range
package is planning input, never evidence of a verified implementation.
Developer-only, release, and judge briefs need no package; proceed to Step 7.

Success prints only the absolute artifact path, not JSON. The file contains
the resolved range, commit list, stat, and patch. Set `TEAMLEAD_REPORTS_DIR`
to use the default range-specific filename instead of passing OUTFILE.
Exit 2 names invalid input; exit 1 names a tool or output failure. On either,
fix the named cause and repeat; never compose verification briefs without a
completed package. Existing different content is preserved.

Proceed immediately to Step 7 with the printed path as `REVIEW_PACKAGE`.

## Step 7 — Compose the Briefs

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
Step 11 to confirm it; use a short reports directory). Exit 3 means the placeholder scan
itself failed, so whether the briefs are clean is unknown: re-run, never
dispatch on it. The placeholder set and both validation directions are the
script's contract; see the header of
`skills/herdr-teamlead/compose-briefs.sh`.

What you decide, and it is the whole of your job here:

- `SHARED_CHECKOUT` — the checkout the workers read.
- `POLICY_INDEX` and `RELEASE_SKILL` — the resolver's absolute artifact paths.
- `AUTHORITY_STATEMENT`, `TASK_AUTHORIZATION`, `AUTHORIZED_ACTIONS`, and
  `EXTERNAL_PERMISSION` — Step 3's verified ownership, actual operator source,
  bounded task actions, and any additional non-owner permission.
- Per role: `ISSUE`, `BRANCH`, `WORKTREE`, `REPORT`, `REPORTS_DIR`, and the
  phase and mode that role runs this round.
- For reviewer and tester: `REVIEW_PACKAGE`, `REVIEW_BASE`, and `REVIEW_HEAD`
  from Step 6. Missing, empty, or non-file package paths refuse composition
  before any brief is written.

| Phase | Role | Mode | Output |
| ----- | ---- | ---- | ------ |
| 1 | reviewer | A | design note on the issue |
| 1 | tester | A or B | test plan, or acceptance tests as a patch |
| 1 | developer | — | implementation, pushed branch, no PR |
| 2 | reviewer | B | COMMENT review of the pushed branch |
| 2 | tester | C | gates plus acceptance tests against the pushed branch |
| 3 | release | — | PR opened, bot rounds answered, merged, branch deleted |

Phase 2 briefs name the branch AND the commit SHA the worker must report
against. A report against an older tip does not gate anything.

Name the issue, file, finding, and report path in full in every brief.
Context retention is limited to the same-role fix rounds in
`rules/agent-team-operation.md` Fix Loops. Fresh-worker fix briefs include
the prior attempt count and the ownership handoff that section requires.
Reviewer and tester verification briefs name `full` or `scoped` review,
the prior findings, and the follow-up issue for new advisories. The final
release-gating verification is `full`. Proceed immediately to Step 8.

## Step 8 — Provision the Worktrees

One call per worker that writes anything:

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
against the shared checkout (`rules/agent-team-operation.md` Writers and
Checkouts). A read-only Phase 1 reviewer needs none. Remove them per
`rules/agent-worktree-isolation.md` Cleanup once the branch lands.

On any non-zero exit, fix the input it names and re-run this step; do not
dispatch a brief whose worktree does not exist. Proceed immediately to Step 9.

## Step 9 — Label the Layout (optional, once per team)

```bash
CP=.tessl/plugins/gamussa/coding-policy; [ -d "$CP" ] || CP="$HOME/$CP"
bash "$CP/skills/herdr-teamlead/label-workspaces.sh" \
  <lead-label> [<agent>=<workspace-id>]...
```

Names the lead's workspace, each worker's workspace after its agent, and each
worker's pane after its kind. With no pairs, the workspaces come from the
roster. Emits `{"lead":{...},"agents":[...]}` with a per-target
`renamed|unchanged|failed`. Exit 3 means at least one rename failed and the
JSON says which. A label failure never stops a round.

Run this once per team, not once per round: a name already in place is
reported `unchanged` and nothing is sent. Skip it on a team whose sidebar is
already named. Proceed immediately to Step 10.
