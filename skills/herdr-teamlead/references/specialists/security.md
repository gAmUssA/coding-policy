# Security

Use when the task changes a trust boundary or creates a concrete security
question, such as who may perform an action or where untrusted input reaches a
sensitive operation. Scope the work to the affected behavior and actual threat.

## Brief inputs

Supply the relevant data flow, assets, actors, trust boundaries and accepted
requirements. State the authorized environment and permitted inspection or
test actions. Name available source analysis and test tools or a relevant
installed security skill. Do not infer testing authority for external systems
from access to their credentials or URLs.

## Working questions

- Which actor controls each input, and where does the system rely on it?
  Trace the affected path through validation, authorization and use.
- Does the proposed check protect the actual sensitive operation, including
  failure and alternate paths? Check the implementation and its consumers.
- Is the concern a demonstrated defect, a supported exploit path, or a
  hypothesis? State prerequisites and realistic impact without inflating them.
- Does the proposed correction restore accepted behavior or add a new
  obligation? Preserve that distinction in findings for the lead.

## Deliverable

Return the bounded threat assessment, findings tied to code or behavior,
prerequisites, impact, and targeted corrections or verification. Use sanitized
evidence; never place secrets or sensitive payloads in a report. State tools,
coverage and uncertainty. A clean focused report establishes only the checked
scope, not whole-system security.

Record any security design or implementation you contributed. Recommend durable
trust-boundary lessons through the lead's existing memory workflow.
