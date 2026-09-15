# Specialist Bench

Compose the active team around the next task decision. A profile is reusable
expertise, not a permanent Herdr pane. Bring a specialist in when its answer or
artifact could change the work; keep unused profiles on the bench without
launching workers. Reassess composition when evidence, scope or the task phase
changes.

## Separate responsibility, specialty and worker

**Responsibility** states what the assignment must deliver: implementation,
investigation, advice, review, verification or release. **Specialty** states
which expertise the assignment needs. **Worker** names the actual execution
session, model, tools and measured capacity. Record all three in the plan and
task ledger. Renaming a worker cannot change what it contributed or grant new
authority.

The normal implementation, reviewer, tester, release and judge contracts remain
in `rules/agent-team-operation.md`. A consultation uses the `advisor`,
`investigator` or `architect` responsibility and produces its bounded deliverable
and report. A specialist who needs to implement receives the normal
developer assignment, with a provisioned writing worktree and the relevant
profile in its brief. A specialist report does not substitute for a required
reviewer or tester pass. A specialist may perform that gate only through its
normal verification responsibility with the required evidence and independence.

Separate requested expertise from proven capability. Before considering
headroom, assess whether a candidate has the tools, relevant available skills,
task context and demonstrated results needed for the assignment. Read its
evidence-linked prior work where available. A model name or profile title alone
is no evidence of competence. Record material capability gaps and their effect
on the requested deliverable. Use measured headroom among suitable candidates;
when none qualifies, record the gap instead of silently assigning an unsuitable
worker. The pinned judge remains outside ordinary staffing.

## Choose relevant profiles

Read only the profiles needed for this task. Most are prompts for selecting
questions and evidence, not checklists that every change must complete. Five
are triggered: the condition fires, the profile is consulted, and its
deliverable lands before implementation proceeds. Four of them allow a
recorded staffing decision instead, with its reason, the way a shortfall of
eligible workers already does. The exhaustion trigger does not: the judge's
diagnosis rules on that assessment, and `teamlead diagnose` refuses without
it.

| Trigger | Profile | Deliverable before implementation |
| --- | --- | --- |
| A new or substantially changed package above the size the repo states | Architect | The intended boundaries, the options and consequences, and the verification each boundary needs |
| A new or changed trust boundary — anything deciding whether foreign input, generated content or a proposed change is safe | Security | A bounded threat assessment against that boundary |
| A new user-facing command, flag or refusal path | UX and product | The flow, the alternatives considered, and acceptance criteria |
| A new user-facing document | Documentation | A draft verified against the shipped behavior, never against intent |
| Fix rounds reaching the allowance without converging | Investigator | A reproduction, a causal assessment and a discriminating experiment. Required; no staffing decision substitutes for it |

The exhaustion trigger is enforced where the judge is dispatched. The other
four are detected from the round's diff.

## Declare this repo's trigger surfaces

`teamlead detect-triggers` classifies a diff against `.herdr/triggers.json` in
the consuming repo. Every field is required; an absent or incomplete
declaration is refused rather than read as "nothing fired", so the architect
trigger no longer fires never or always depending on who reads it.

```json
{
  "schema_version": 1,
  "package_roots": ["skills/*", "scripts"],
  "package_change_lines": 400,
  "trust_boundary_paths": [".github/workflows/*", "hooks/*"],
  "cli_spec_paths": ["skills/herdr-teamlead/teamlead/*.py"],
  "cli_surface_markers": ["add_parser(", "add_argument("],
  "user_doc_paths": ["README.md", "docs/*"]
}
```

`package_roots` name the directories this repo treats as packages, and the
nearest matching ancestor owns a changed file. A package root is a directory
rather than a subtree, so `*` matches within one path segment there: `skills/*`
is every skill, never a directory nested inside one. `package_change_lines` is
the size a changed package must exceed to trigger the architect.
`trust_boundary_paths`, `cli_spec_paths` and `user_doc_paths` are subtree
globs, where `*` does span path separators, so `docs/*` covers everything under
`docs`. `cli_surface_markers` are literal substrings that an
added line inside a CLI spec path must carry to count as a new command or flag.
State `[]` for a surface this repo does not have — an empty list is a statement,
an omitted field is not. Classification rules are in
`skills/herdr-teamlead/teamlead/triggers.py`, in its module docstring and the
`detect` and `cli_surface` docstrings.

Run it before `plan`, with the roles and requirements that round intends:

```bash
teamlead detect-triggers --repo <dir> --base <ref> [--head <ref>] \
  --roles <role[,role...]> [--requirements <file>] [--planned <file>] \
  [--decisions <file>]
```

## Declare a pre-implementation round's surfaces

The triggers gate work before implementation, and a task's first round has
nothing committed to classify. `--planned` supplies the surfaces the work will
touch, classified against the same declaration. A round that classifies neither
a diff nor a plan is refused.

```json
{
  "schema_version": 1,
  "added": ["skills/new-thing/mod.py", "docs/new-guide.md"],
  "changed": ["skills/herdr-teamlead/teamlead/recovery.py"],
  "package_lines": {"skills/herdr-teamlead": 800},
  "cli_surface": ["skills/herdr-teamlead/teamlead/cli.py"]
}
```

`added` and `changed` are repo-relative paths the round will create or edit.
`package_lines` states the lines the round will change in a package, for the
architect trigger's size. `cli_surface` names the declared CLI spec paths the
round will add a command, flag or refusal to; a path outside
`cli_spec_paths` is refused. State `[]` or `{}` for what this round has none
of. A later round classifies its diff, which is evidence rather than intent.

Exit 0 means every fired trigger is staffed or answered. Exit 1 with an
`unaddressed_trigger` error names the triggers that are neither; re-run it
after each change, since the failed invocation read none of them. A trigger is
answered by planning its role or by a requirements assignment carrying its
specialty; which role and which specialty answer each trigger are the
`TRIGGER_ROLES` and `TRIGGER_SPECIALTIES` constants in
`skills/herdr-teamlead/teamlead/triggers.py`, and the detection payload names
the one it accepted.

A staffing decision answers a fired trigger instead, and the detector reads it:

```json
{
  "schema_version": 1,
  "decisions": {
    "documentation": "the added file is an internal reference, not a reader-facing document"
  }
}
```

An empty reason is refused. Silence is never that decision. A decision for a
trigger that did not fire is reported under `unused_decisions` and changes
nothing.

| Profile | Bring it in for | Useful output |
| --- | --- | --- |
| [UX and product](specialists/ux-product.md) | A new flow, confusing behavior or unresolved interaction choice | Concrete flow, alternatives and acceptance criteria |
| [Accessibility](specialists/accessibility.md) | An affected user path needs keyboard or assistive technology evidence | Reproducible findings with coverage and manual-check gaps |
| [Investigator](specialists/investigator.md) | Unclear causality or repeated unsuccessful fixes, and every exhausted allowance | Reproduction, causal assessment and discriminating experiment |
| [Architect](specialists/architect.md) | Cross-component choices or lasting contracts | Decision note with options, consequences and verification needs |
| [Security](specialists/security.md) | A changed trust boundary or concrete security question | Bounded threat assessment and actionable findings |
| [Performance and reliability](specialists/performance-reliability.md) | Latency, concurrency, resource or recovery uncertainty | Measured explanation and reproducible failure or improvement check |
| [Documentation](specialists/documentation.md) | Readers must understand or operate changed behavior | Verified draft or findings against the actual workflow |

Combine compatible expertise in one bounded assignment when a worker can cover
it. For example, a UX worker with the required tools may assess accessibility
on the same flow. Split the work when the evidence, tools or independence
requirements differ. The bench is extensible: describe another specialty when
the task needs one, with its capability evidence and concrete deliverable.

## Record assignment requirements

Worker configuration schema v3 carries a `capabilities` list for each worker.
Keep these declarations aligned with the tools, skills and evidence inspected
above. A capability label is a staffing input, not a credential or authorization.
A `capabilities` change never follows one provider refusal: one stopped session
read repeatedly is one refusal. Remove a capability only on independent
refusals of the same class across sessions, each recorded through
`record-refusal`, and cite those dispatches in the change.
Follow `skills/herdr-teamlead/state-schema.md` for configuration and persisted
assignment shapes.

Pass a requirements file to `plan --requirements <absolute-file>`. The file
names the expertise needed for each specialized assignment and the stable
engagement that a later consultation may continue. For example:

```json
{
  "schema_version": 1,
  "assignments": {
    "advisor": {
      "specialty": "ux-product",
      "required_capabilities": ["ux"],
      "independent": false,
      "engagement": "onboarding-ux"
    }
  }
}
```

Use the planned role as the assignment key. `required_capabilities` names the
worker capabilities the deliverable needs. `independent` states whether the
assignment requires an independent worker; the reviewer and tester gates retain
independence. `engagement` identifies the bounded consultation across its
follow-ups, not a new task or correction budget. Give each distinct engagement
its own identity and preserve the parent task identity.

The input parser in `teamlead/composition.py` owns the accepted keys and names.
Reviewer and tester requirements use `independent: true`. New advisor and
investigator assignments require a requirements file; supply it for every new
architect consultation too. Legacy architect records remain readable but establish
no specialist continuity. No extra model flags or invented role aliases belong
in a requirements file.

Include requirements for specialist consultations and for a developer, reviewer
or tester whose assignment needs that expertise. The planner emits the
requirements with its assignment; dispatch rechecks them from that saved plan.
Use that plan through the normal apply contract. On a refusal, resolve the
reported capability, independence or evidence gap before replanning. Do not
hand-edit the plan to substitute a worker or bypass its requirements.
Live specialized dispatch requires the lead's existing supervision binding and
one enrolled report path per responsibility. Use the same state selection across
plan, apply, assessment, observation and follow-up.

## Compose a bounded consultation

State the question the specialist must settle, why its answer matters now, the
accepted behavior, scope, relevant prior decisions, available inputs, permitted
actions and stopping condition. Define what the lead will inspect to accept
the result. Supply the source paths and revisions, useful project lessons, and
the selected profile's applicable questions in the brief. The worker should
not need an earlier conversation to reconstruct its assignment.

Name actual available tools and skill invocations when they improve the work.
Check availability before claiming a capability. Missing tools are an evidence
gap to resolve or report; do not claim visual inspection, user research,
assistive technology coverage or measurements that the worker cannot perform.
Avoid activating adjacent specialist workflows solely from a changed filename.

Compose each consultation with `templates/brief-specialist.md` through the
composer; its canonical consultation roles select that shared template. Supply
`TASK`, `SPECIALTY`, `RESPONSIBILITY`, `OBJECTIVE`, `ACCEPTANCE_CRITERIA`, `INPUTS`,
`TOOLS_AND_SKILLS`, `SCOPE_LIMITS`, `CONTRIBUTION_HISTORY`, `KNOWLEDGE` and `REPORT`,
plus the common task, branch and authority values. Set `RESPONSIBILITY` to the
planned role. Put applicable profile questions and evidence in the actual
brief values; a bare profile name is insufficient. For a developer, reviewer or
tester with specialist requirements, supply the same bounded expertise context
in `SPECIALIST_CONTEXT` within its normal role brief.

Consultations read evidence and write only their assigned report or draft
artifacts. Follow the normal composition, authority classification,
YOLO verification, retrospective, ledger and fleet-supervision steps for every
dispatch. A specialty adds no independent permission, correction attempt or
release waiver. Stop a consultation once its assigned deliverable is ready or
its genuine block is recorded; idle bench membership creates no monitoring job.

## Assess specialist work

Confirm delivery through the normal report checkpoint and save its successful
JSON output. Read the actual report in full. Inspect the requested artifact and
source evidence, then assess the assignment outcome and what the worker
contributed. A worker's contribution claim is an input to that judgment, not
the judgment itself.

Run the installed `skills/herdr-teamlead/teamlead.sh` with explicit `bash` and
the plugin root resolved by the skill. This synopsis names its owner command:

```text
assess-specialist --record /durable/team/assessment.json [--state <state-file>] [--now <ISO-time>]
```

The input supplies the actual per-assignment dispatch id from apply output or
the supervision member, the worker's report path, and the saved delivery path:

```json
{
  "id": "onboarding-ux-assessment-1",
  "dispatch": "onboarding-ux-dispatch-1",
  "report": "/durable/team/reports/ux-1.md",
  "delivery": "/durable/team/reports/ux-1-delivery.json",
  "outcome": "The requested interaction proposal is complete; implementation remains open.",
  "contribution": "design",
  "summary": "The report proposes the chosen recovery flow and supplies acceptance criteria."
}
```

`outcome` and `summary` record the lead's actual assessment and rationale.
`contribution` is `none`, `design` or `implementation`; classify the substantive
work rather than its current role. The delivery file must be the successful
`wait-report.sh` JSON receipt for that worker and report, with `found: true`, or
the unchanged owner `recover-report` result for that exact dispatch and report.
For native delivery missed by the watcher, complete recovery under
`references/dispatch-recovery.md` first and save its actual output. The assessment
owner checks recovered output against the saved recovery record; an edited or
invented receipt does not establish delivery.
Use an actual dispatch identity and matching report path; never invent history
for a report that has not been reconciled with its assignment.

The command emits JSON and preserves an immutable assessment with report and
delivery receipts in the existing owner state. It binds the saved bytes to the
assignment; it cannot infer semantic truth from report prose. Handle any
non-zero diagnostic before continuing. Keep the referenced evidence files for
future verification.
An exact retry with the same assessment id returns its original record. A new
assessment uses a new id and preserves the old evidence. The command also accepts
confirmed reviewer or tester dispatches when their contribution needs assessment;
developer work remains on the normal developer receipt and correction path.

Record the assessed outcome and evidence in the task ledger. Handle pending
supervision events and retire the preceding enrollment through
`skills/herdr-teamlead/references/supervision.md`. Assessment, event
acknowledgement and enrollment retirement are distinct operations. None of them
accepts the whole task or replaces its verification and release gates.

Before requesting a retained specialist follow-up, save the assessment and
finish those observation obligations. Preserve the assessed report and delivery
bytes; changed or missing source evidence requires reconciliation. Follow the
dispatch recovery contract for the continuation command and its refusal
conditions.

## Preserve contribution history and knowledge

Track actual contributions across session clears, worker changes and model
changes. Record who originated or materially shaped the accepted design, wrote
the implementation, or authored the artifact under review. Consultation that
only inspects evidence may remain independent; a specialist that shaped the
solution must disclose that contribution. Decide independence against the
subject being verified, never against the worker's current title or a fresh
context. Obtain another qualified worker for independent assessment of a
contributor's work. Keep the ordinary reviewer and tester gates intact.

An unassessed consultation is unresolved contribution history. Assess it before
relying on that worker's independence. A `none` assessment records that the lead
verified no contributing work; a design or implementation contribution remains
part of the task history across role, model and session changes. Obtain a
different qualified worker for the independent gate when the subject includes
that contribution.

New reviewer assignments carry an explicit verification scope. Migrated reviewer
history retains unknown scope, and architecture work remains a possible
contribution until assessed against actual output. The owner never infers
independence from a newer schema stamp. External authors and work without usable
task provenance still need the lead's explicit exclusions. Follow the planning
contract in `references/round-setup.md` Step 5 rather than reclassifying history
from a worker's current label.

Keep a useful worker idle after its report when follow-up is likely and capacity
permits. An idle session is optional continuity, not durable memory or authority
to dispatch into it without checks. Use the verified specialist continuation
path for a follow-up within the same engagement; follow the dispatch recovery
contract for its inputs, refusal conditions and evidence. Every other next
assignment follows the normal clear or developer retained-fix path. Before
clearing, relaunching or changing a seat or tier, complete the required
retrospective and capture useful outgoing knowledge.

Use `skills/herdr-teamlead/references/working-memory.md` for project lessons and
lead handoffs. Keep a specialty label consistent, such as `specialty:ux-product`,
alongside the project and task labels. The memory owner's scope selection is a
union; inspect each returned lesson's scope before applying it to a different
project. Revalidate source evidence before including a lesson in a fresh brief.
Workers propose lessons in their reports; the lead curates them through the
existing owner. Keep task outcomes, user attention and immutable retrospective
notes in their existing owner artifacts.

## Evaluate the composition

At the required retrospective, assess which specialist contribution changed a
decision, prevented rework or exposed a missed requirement. Note expertise that
arrived too late and consultations that produced no useful change. Compare the
observed benefit with waiting time, repeated context setup and capacity use;
mark unavailable measurements as unknown. Propose a concrete composition change
with an owner and success criterion, then revisit it in the next applicable
retrospective. A new profile or durable lesson should follow evidence of useful
work, not a desire to fill every possible seat.
