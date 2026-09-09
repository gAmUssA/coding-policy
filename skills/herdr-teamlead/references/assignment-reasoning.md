# Assignment Reasoning

The lead applies this reference at intake, when composing a bug-fix brief, and
when assessing findings before a correction. The worker receives the relevant
questions in its self-contained brief. Reasoning stays with the lead and workers;
the dispatch utility does not infer intent or causality from prose.

## Preserve the Accepted Behavior

Record the operator's actual request and later decisions separately from the
lead's proposed implementation. Name the observable behavior or deliverable the
task must provide, its acceptance criteria, and any explicit limits. Use the
existing task identity and authorization record throughout corrections.

Resolve ordinary implementation choices within that accepted behavior. A hard
implementation, an additional necessary file, or a required regression test does
not by itself create a new permission requirement. A diagnostic recommendation
alone does not authorize implementation when the request was investigation only.
State investigation-only intent explicitly in the role's task text and select
the developer template's investigation branch. Its deliverable is a report;
implementation, push, PR and release instructions do not apply to that branch.

Gate investigation reports against the requested knowledge and evidence, including
required independent checks. The implementation phases in Review Before PR apply
only to authorized code changes. Preserve blocking-finding, correction-allowance,
and judge rules for the investigation. A judge ruling returns the investigation
to its knowledge-deliverable gate; it never supplies code-release authority.

## Assess a Finding's Scope

Read the full finding and identify the behavior that implementing it would commit
the project to provide. Record the applicable acceptance criterion and classify
the proposed change as one of:

- **Required correction:** restores or completes accepted behavior, including
  necessary downstream tests and documentation. Continue within existing task
  authority and the remaining correction allowance.
- **Contract expansion:** introduces behavior or an obligation not required by
  accepted intent, such as a new guarantee, subsystem, supported environment, or
  continuing monitoring duty. Record it as a proposal for the operator.
- **Unresolved interpretation:** the evidence does not settle the requested
  behavior or whether the proposed fix is necessary. Name that uncertainty.

A reviewer's label or confidence does not amend the accepted contract. Scope
classification also does not dismiss a blocking finding: a contested verdict,
lead override, or bot disagreement follows the existing judge path in
`rules/agent-team-operation.md`. The lead cannot waive a finding by calling it
an expansion. An agreed correction still obeys the fix allowance and release
gates; this reference adds no attempts or substitute judge trigger.

When an operator decision is needed, save an attention item before presenting it.
State the original requirement, proposed expansion, smallest compliant option,
consequences of each choice, and a recommendation. Reuse a prior decision when
its scope still covers the action. Continue independent authorized work while
the affected task waits.

When successive findings concern the same underlying design, compare their
causes and the actual progress before proposing another fix. Record whether
the changes close distinct defects or keep compensating for an unresolved
assumption. Put that evidence in the next brief, existing judge dispute, or
operator proposal as applicable. Preserve the cumulative attempt count.

## Diagnose the User's Failure

For a bug task, ask the developer and tester for a reproduction matching the
actual user path. Record expected and observed behavior, setup, inputs, and
repeatability. If a faithful reproduction is unavailable, state the limitation
and what the substitute does and does not establish.

Separate the initiating trigger, any condition that hides or exposes the fault,
and the symptom the user sees. Compare the failing path with a demonstrated
working path and locate the earliest relevant difference. Inspect history when
it can explain that difference; proximity to a recent commit is not causal proof.

Name the leading explanation and an observation that would disprove it. Run the
smallest feasible counterfactual, changing one relevant condition at a time,
and retain contradictory results. Explain how the proposed cause accounts for
both paths. Label facts, hypotheses, and unresolved uncertainties separately.

## Use the Evidence

Before accepting a diagnosis, the lead checks that its cause explains the
reproduction and comparison evidence. A missing experiment is an explicit gap,
not an invented pass. Obtain a focused investigation when the gap could change
what should be fixed. Keep bounded research inside an already authorized bug
fix when it does not require a separate deliverable or decision.

When implementation is authorized, turn the reproduction into an appropriate
regression test. The tester verifies the failing behavior before the fix and the
expected behavior after it where feasible, and records any limitation. Retain
the evidence in the normal report so the retrospective can examine failed
assumptions and successful diagnostic methods.
