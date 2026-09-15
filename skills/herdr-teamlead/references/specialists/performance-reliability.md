# Performance and Reliability

Use for observed latency, resource pressure, concurrency faults, or recovery
behavior that the ordinary implementation and test plan do not yet explain.
Give the specialist a measurable question tied to the user's workload.

## Brief inputs

Supply expected behavior, workload and environment, baseline measurements,
affected revisions, and known limits. Define permitted load or fault injection
and where it may run. Name available profiling, telemetry and test tools; state
which measurements are unavailable. Production experiments require the task's
existing authorization for those actions.

## Working questions

- Does the measurement represent the user's path and workload? Distinguish
  environment noise, cold-start effects and steady behavior when relevant.
- Where is the earliest measured bottleneck or loss of progress? Compare a
  working case and test the explanation before prescribing a rewrite.
- What happens when an operation repeats, overlaps, is interrupted or resumes?
  Check the task's required recovery and resource-ownership behavior.
- Which change could improve the measure while breaking correctness or another
  accepted constraint? Include the relevant verification.

## Deliverable

Return reproducible commands or experiment descriptions, inputs, environment,
baseline and observed measurements, a supported explanation and the smallest
recommended change. For recovery work, include the failure scenario and
observed result. Distinguish results from projections; do not claim an unrun
benchmark improved. Name limitations and a regression check that fits the task.

Record any proposed design or code contribution. Offer lessons with workload
and environment scope so the lead does not generalize a local result to every
deployment.
