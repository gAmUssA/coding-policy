---
bump: minor
---
### Added

- **Review lanes classified by login plus body marker** (`skills/release/review-lanes.sh`, ADR 0010). A Claude code-quality reviewer will replace Copilot and post as `github-actions[bot]`, the same login as the Codex policy reviewer. Without lanes, a clean Claude comment would dismiss a blocking Codex `CHANGES_REQUESTED` and the watcher would read it as the Codex verdict. Poll, watch and dismiss now read each verdict per lane. The snapshot gains a `lanes` view with a `gating` flag, and `reviews.codex` / `reviews.copilot` keep their shape. Dismissal needs a later all-clear from the same lane. The Claude lane is advisory (`CLAUDE_QUALITY_GATES=false`) until ADR 0010's promotion criteria are met. Unmarked older Codex reviews classify as Codex by login. Adapted from upstream jbaruch/coding-policy ac184669 (#776).

### Changed

- **The Codex policy reviewer's body opens with a lane marker.** `skills/onboard-repo/templates/post-review.sh` prefixes `<!-- coding-policy:codex-policy-review:v1 -->`, which GitHub does not render. Consumers pick it up on the next `onboard-repo` upgrade; until then their unmarked reviews classify as Codex by login.
