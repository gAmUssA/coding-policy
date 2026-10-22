---
bump: patch
---
### Fixed

- **A new plugin's first release can capture its registry baseline.** The versions API answers a plugin with no versions with a structured `{"error":{"status":404,"title":"Not Found"}}` body and `tessl api` exits 1. `registry-version.sh` treated that as a tool failure, and `capture-registry-baseline.sh` parsed `tessl plugin info`, which answers the same case with prose and exit 1. Either way the release stopped before its first merge. `registry-version.sh` now classifies that exact body (top level holds only `error`; `error` holds only `status`/`title`/`message`; numeric 404; title `Not Found`) as never published before checking the exit status. Any other 404 shape, a 401, a 5xx, a transport failure or an unparseable body still fails.
- **`capture-registry-baseline.sh` reads the versions API and can emit `unpublished`.** It sources `registry-version.sh` instead of parsing `tessl plugin info`, and prints `{"version":"unpublished"}` for a verified never-published plugin. It never prints an empty or guessed baseline.
- **`verify-publish-landed.sh` accepts the `unpublished` baseline.** From it, conjunct 2 requires the registry to show any published version: a still-absent plugin after a successful run is exit 1 (did not land), never a vacuous pass. A baseline that is neither numeric `MAJOR.MINOR.PATCH` nor `unpublished` (a stray `null` from `jq -r`, a prerelease) is now an argument error, exit 2. `skills/release/SKILL.md` Step 7 names the sentinel.
- Proven with a fake `tessl` (and `gh`) on PATH: the structured 404 is accepted, the reject cases still exit 2, and the first-publish landed check passes, fails on a still-absent plugin, and fails on a failed run. The accept cases fail on the previous scripts.
- Adapted from upstream jbaruch/coding-policy 6bc2d0f4.
