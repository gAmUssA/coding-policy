---
bump: patch
---
### Fixed

- **Keep Git stderr out of Rex team error messages.** All three `git_failed` paths now identify the failed command and repository path with fixed recovery guidance, preventing credentials printed by clean filters or Git configuration errors from reaching the JSON envelope. Regression tests cover the shared Git helper, sparse-checkout detection and a real failing clean filter, and fail when raw stderr is restored.
