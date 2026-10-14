---
bump: patch
---
### Fixed

- **Four review advisories deferred from #42 and #43 are closed**.
  - `teamlead preflight` refuses a malformed or timezone-less `--now` before any worker contact. Before, it reached the retrospective check after the roster and authority calls, and `measure` could store it as `measured_at`.
  - `scripts/assemble-release.sh` exits 1 when `changelog.d/` is missing; before, it reported a no-op release and silently stopped assembling.
  - `scripts/check-changelog-fragment.sh` rejects a PR that deletes `CHANGELOG.md` or `changelog.d/README.md`, which the post-merge assembly needs.
  - Release skill Step 7 captures `publish-outcome.sh`'s exit status separately from its JSON. Before, `| jq` hid the indeterminate exit 2.

  Each code fix's new case fails with the fix reverted.
