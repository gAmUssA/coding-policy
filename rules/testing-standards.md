---
alwaysApply: true
---

# Testing Standards

## Scope

- Governs code the project ships: the published artifact, everything CI builds from it, and the tests covering both
- Narrow exception for an instrument tree.
- Instrument tree: code whose OUTPUT is the deliverable, read by a human (live-upstream probes, reverse-engineering scratch, one-off measurement scripts)
- An instrument tree falls outside this rule in full — every section below, not Coverage alone
- Preconditions (all required):
  1. The project's publish-exclude manifest lists the tree (`.tesslignore`, `.npmignore`, `MANIFEST.in`) — readable from the repo under review, never asserted in prose
  2. No shipped module imports from the tree
  3. Deterministic logic the tree's findings rest on carries tests, wherever those tests live
- A module the published artifact reaches does NOT qualify, whatever directory holds it
- "It is scratch" does NOT qualify on its own — all three preconditions are required
- Every other tree in the repo follows this rule in full

## Coverage

- Every module gets tests — no untested code ships (narrow exception: Platform-Bound Untestable Carve-Out below)
- Test file naming follows the project's convention, or the Stack Conventions table when the project has none
- Narrow exception for code that cannot execute on the project's CI runners.
- Applies when the code drives an external desktop app, OS-level automation, or a proprietary runtime the runners cannot host (GUI automation gated on OS consent, a device-only hardware API)
- Preconditions (all required):
  1. The deterministic, CI-runnable pieces are extracted and unit-tested — parsing, normalization, and data-shaping helpers split out of the automation wrapper. Only the genuinely-unhostable layer is exempt
  2. A manual validation procedure for the exempt layer is documented — what to run, what to observe, what counts as a pass
  3. The project's README or contributor doc names this carve-out, each exempt artifact, and where its validation procedure lives
- "Hard to install in CI" does NOT qualify — install the tool and test it per `rules/ci-safety.md` Install, Don't Skip
- Every other module still ships tests that run in CI

## Stack Conventions

| Stack | Framework | Location / naming |
|-------|-----------|-------------------|
| JVM (Kotlin, Java) | JUnit 5 or Kotest | `src/test/**`, `*Test.kt` / `*Test.java` |
| Swift | Swift Testing; XCTest for UI tests | `*Tests.swift`; no `sleep()` in tests |
| TypeScript | vitest or jest | `*.test.ts` beside the module |
| Python | pytest | `tests/test_*.py` |
| Shell | bash harness | `tests/test_*.sh`, discovered by `scripts/run-tests.sh` |

- An existing project's convention wins over this table

## Assertions

- Assert **outcomes**, not implementation details
- Test what the code does, not how it does it
- If an internal refactor breaks your tests, the tests were testing the wrong thing

## Determinism

- Tests must be deterministic — no self-generated random test data (narrow exception: seeded property-based tests below)
- Provide fixed test data; never let runtime randomness generate or shape the test's inputs
- No dependence on the current date or wall-clock time — no `today`, runtime `now()`, or hardcoded future dates in assertions or fixtures
- Control the clock: inject or freeze "now" (a passed-in reference date, a mocked time source, a `Clock` instance) so a test green today is green every day
- Fixed past dates as fixtures are fine — the ban is on time-relative values that rot as the run date advances
- Flaky tests are bugs — diagnose the root cause, don't retry and hope
- Narrow exception for property-based tests whose generated inputs come from a pinned seed.
- Applies when a generative testing library (Kotest property tests, Hypothesis, fast-check, SwiftCheck) explores many cases per run under a fixed seed
- Preconditions (all required):
  1. The generator seed is a constant in the test file (e.g. `PropTestConfig(seed = 1234)`), never drawn from the clock, the environment, or an unset default
  2. The iteration count is explicitly bounded (e.g. `iterations = 100`), never unbounded or environment-derived
  3. No runtime-RNG call (`random()`, unseeded `Random()`, `shuffle`) selects or shapes the inputs
- Every other case still follows Determinism: unseeded or unbounded generation, and any runtime-RNG input, stays forbidden

## Fixtures and Independence

- No binary fixtures checked into the repo
- Build test data programmatically in test setup/fixtures
- For binary files that can't be built programmatically, download them from a URL during test setup
- Each test must run independently — no shared mutable state between tests
- Test order must not matter
- Clean up after yourself: temporary files, database state, mock patches
