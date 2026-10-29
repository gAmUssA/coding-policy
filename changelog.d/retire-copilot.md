---
bump: major
---
### Removed

- **The Copilot reviewer plumbing**. `skills/release/request-copilot-review.sh` and its test are deleted, and the release skill no longer requests Copilot after a push. The Claude code-quality lane (`.github/workflows/review-claude.yml`) replaced it; `docs/adr/0014-retire-copilot-lane.md` records the decision and supersedes `docs/adr/0010-review-lanes.md`.
- **The `copilot` review lane**. `skills/release/review-lanes.sh` drops `COPILOT_LOGINS`, `COPILOT_GATES` and the lane's config entry; a review from `copilot-pull-request-reviewer[bot]` now classifies as no lane and is never dismissed by script. `test_review_lanes.sh` and `test_dismiss_stale_reviews.sh` cover it.
- **Copilot fields in the review snapshot**. `poll-pr-reviews.sh` drops `reviews.copilot`, `lanes.copilot` and `inline_comments.copilot` with no compatibility stub, and `watch-pr-reviews.sh` drops the `review_unrequested` result. `reviews.codex.requested` stays.
- **The scaffolded `.github/copilot-instructions.md`**. `onboard-repo` no longer writes it and its template is deleted; a consumer's existing copy is neither a target nor removed, which `test_scaffold.sh` and `test_preflight.sh` cover.

### Changed

- **Reviews outside every lane stay readable**. `poll-pr-reviews.sh` adds `other_reviews` (the active verdict per unclassified login, with `is_bot`; a later `COMMENTED` never clears a `CHANGES_REQUESTED`, only `APPROVED` or a dismissal does, and `reviews[]` keeps every body) and `inline_comments.other`, so Copilot and human bodies and comments still reach Step 6. Any `CHANGES_REQUESTED` there, human or bot, makes `watch-pr-reviews.sh` return `changes_requested` until resolved through review, matching `rules/ci-safety.md` Never Skip Tests; `rules/review-severity.md` drops its "Copilot never gates" bullet for that bullet.
- **Eval scenario-3 scores Claude findings by substance**. The input-validation finding is blocking and the docstring finding advisory, whatever the lane's gating flag.
- **The watcher waits only for owed verdicts**. It waits for every gating lane, and for the advisory Claude lane only when `lanes["claude-quality"].expected` is true: `review-lanes.sh` names its workflow (`CLAUDE_QUALITY_WORKFLOW`) and a check from it ran on head. A repo without the Claude reviewer never stalls, and one with it never reaches `ready` before the Claude body posts. `test_review_lanes.sh` pins the workflow name to `.github/workflows/review-claude.yml`.
- **The Claude lane's promotion points at ADR 0014** in `rules/review-severity.md`.
