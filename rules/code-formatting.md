---
alwaysApply: true
---

# Code Formatting

## Use the Project's Tools

- Use whatever formatter and linter the project already has configured
- Don't introduce a new formatter without team consensus
- Don't bikeshed style — let the tooling decide

## Stack Defaults

- Defaults for a project with no formatter or linter configured; an existing project's configured tool always wins

| Stack | Formatter | Linter |
|-------|-----------|--------|
| Kotlin | Spotless with ktlint | detekt |
| Java | Spotless with google-java-format | Error Prone or `-Xlint` |
| Swift | swift-format or SwiftFormat | SwiftLint |
| TypeScript | Prettier | ESLint |
| Python | Ruff (`ruff format`) | Ruff (`ruff check`) |
| Shell | shfmt | shellcheck |

## CI Integration

- Run format/lint checks before tests in CI — catch style issues early and cheaply
- Format checks should be a separate CI step that runs fast

## Separation of Concerns

- Never mix formatting changes with functional changes in the same commit
- If code needs reformatting, do it in a dedicated commit before or after the functional change
- This keeps diffs reviewable and bisectable

## Basics

- Remove trailing whitespace
- End files with a single newline
- Use consistent indentation (whatever the project standard is)
- These should be enforced by editor config or pre-commit hooks, not manual effort
