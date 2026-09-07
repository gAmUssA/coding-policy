---
alwaysApply: true
description: Done means a shown passing run — execute the thing, show the output, report it faithfully
---

# Verify Before Done

## Run It

- Execute the build, test, or command that proves the change and show its output
- "Should work", "this will pass", "I'm confident" describe a belief, not a state
- A task without a shown passing run is not done — report it as in progress, naming what has not run
- When the proof cannot run in the current environment, say so and name where it can run

## Report Faithfully

- Failing output is reported verbatim — the command, the exit code, the error text
- A skipped step is named as skipped, never folded into "done"
- No "verified" without the command and its result in the same report
- A partial result is reported as partial: what passed, what failed, what was not attempted

## Scope of Proof

- Logic change → the unit tests that cover it, run and green
- UI or app change → a real launch, a screenshot, or a driven UI check — a compiling build alone proves nothing about the screen
- API change → a real request against the running service and its response
- CI or workflow change → the CI run itself, watched to its conclusion
- Script change → the script executed against a fixture, plus its test harness
- Name what was run in the report; the reader should be able to rerun it

## Relationship to Other Rules

- `rules/ci-safety.md` Always Watch CI is the CI-side form of this rule
- `rules/response-clarity.md` Show State and Progress governs how the proof is presented
- `rules/testing-standards.md` governs the tests that serve as proof
