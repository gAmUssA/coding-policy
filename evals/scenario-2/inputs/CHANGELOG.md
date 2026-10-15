# Changelog

## 1.4.2 — 2026-09-15

### Fixed
- Corrected an edge case in pre-commit hook when staged files include symlinks

## 1.4.1 — 2026-08-30

### Fixed
- Resolved a false positive in the dependency version checker

## 1.4.0 — 2026-08-12

### Added
- New `code-review` skill for automated diff analysis
- Support for monorepo layouts in `release` skill

### Changed
- `testing-standards.md` updated with stricter coverage thresholds

## 1.3.0 — 2026-07-01

### Added
- `dependency-management.md` rule for pinning third-party packages

## 1.2.0 — 2026-06-10

### Added
- `error-handling.md` rule covering retry and fallback patterns

## 1.1.0 — 2026-05-20

### Added
- Initial `release` skill for structured PR-based shipping

## 1.0.0 — 2026-04-01

### Added
- Initial plugin release with core rules and hooks
