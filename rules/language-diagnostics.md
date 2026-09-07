---
alwaysApply: true
---

# Language Diagnostics

## Enable the Language Server

- Every project turns on the diagnostics engine appropriate to its language
- Enable it in-editor for the human and in-loop for the agent — Claude Code and other coding CLIs expose the language server (the `LSP` tool), so the agent reads diagnostics on the files it touches as it works
- Configure the engine for the project's module layout so references resolve (see Resolve Modules First)
- Use whatever engine the project already has — don't introduce a second one without consensus

| Stack | Engine (editor + headless gate) |
|-------|---------------------------------|
| Kotlin | kotlinc with `allWarningsAsErrors`, detekt |
| Java | `javac -Xlint:all -Werror` or Error Prone |
| Swift | Xcode build with `SWIFT_TREAT_WARNINGS_AS_ERRORS`, SwiftLint |
| TypeScript | `tsc --noEmit` under `strict` |
| Python | pyright |
| Shell | shellcheck |

## Findings Are Non-Dismissible Without Cause

- A diagnostic is fixed, or suppressed inline with a stated reason — never silently ignored
- The cause rides next to the suppression — a one-line comment naming why the finding is wrong or unreachable, not a bare ignore directive
- No blanket or file-wide silencing — `# type: ignore`, `// @ts-nocheck`, `@Suppress("ALL")`, `// swiftlint:disable all` switch the engine off for a whole file and mask the same signal it exists to catch
- Prefer a typed helper that proves the invariant once over scattered ignores

## Gate It Deterministically

- CI runs the engine's headless form as a gate at zero findings, before tests, alongside format/lint (see `rules/code-formatting.md` CI Integration)
- In this repo the gate is `scripts/run-diagnostics.sh`; `hooks/stop-handoff-hygiene.sh` runs the same engines over the changed set before handoff
- "Don't ignore the warnings" enforced by memory is not a gate — a deterministic check nobody runs does not exist (see `rules/script-delegation.md`)
- Before handoff, the agent runs the same gate command CI runs and clears every finding, scoped to the changed set only where the gate supports it
- CI runs the same gate as the backstop, never the first place a finding surfaces

## Resolve Modules First

- Configure the engine so imports resolve before turning strictness up — unresolved modules stop deep checking, so real bugs hide behind resolution false-positives
- Match the config to the project's module layout — pyright `executionEnvironments`, tsc `paths`/project references, Gradle source sets
- Resolution is what unlocks the real findings — null-flow crashes, optional misuse, unchecked casts

## Adopting on a Dirty Tree

- Turning the gate on for a tree never checked is its own focused change — land the config plus the fixes in a PR separate from feature work
- The tree goes green first; wire the gate into CI only once it reports zero findings
- Sequence large adoptions: a config PR, then fix PRs grouped by finding shape, then the CI-gate PR
