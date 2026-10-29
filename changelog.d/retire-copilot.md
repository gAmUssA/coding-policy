---
bump: major
---
### Removed

- **The Copilot reviewer plumbing**. `skills/release/request-copilot-review.sh` and its test are deleted, and the release skill no longer requests Copilot after a push. The Claude code-quality lane (`.github/workflows/review-claude.yml`) replaced it; `docs/adr/0014-retire-copilot-lane.md` records the decision and supersedes `docs/adr/0010-review-lanes.md`.
- **The `copilot` review lane**. `skills/release/review-lanes.sh` drops `COPILOT_LOGINS`, `COPILOT_GATES` and the lane's config entry; a review from `copilot-pull-request-reviewer[bot]` now classifies as no lane, never gates and is never dismissed. `test_review_lanes.sh`, `test_poll_pr_reviews.sh`, `test_watch_pr_reviews.sh` and `test_dismiss_stale_reviews.sh` assert a Copilot review never gates.
- **Copilot fields in the review snapshot**. `poll-pr-reviews.sh` drops `reviews.copilot`, `lanes.copilot` and `inline_comments.copilot` with no compatibility stub, and `watch-pr-reviews.sh` drops the `review_unrequested` result. `reviews.codex.requested` stays.
- **The scaffolded `.github/copilot-instructions.md`**. `onboard-repo` no longer writes it and its template is deleted; a consumer's existing copy is neither a target nor removed, which `test_scaffold.sh` and `test_preflight.sh` cover.

### Changed

- **Reviews outside every lane stay readable**. `poll-pr-reviews.sh` adds `other_reviews` (latest review per unclassified login, with `is_bot`) and `inline_comments.other`, so Copilot and human bodies and comments still reach Step 6. A human reviewer's `CHANGES_REQUESTED` makes `watch-pr-reviews.sh` return `changes_requested`, matching `rules/ci-safety.md`; `rules/review-severity.md` now scopes the non-gating bullet to bots.
- **Eval scenario-3 scores Claude findings by substance**. The input-validation finding is blocking and the docstring finding advisory, whatever the lane's gating flag.
- **The watcher reaches `ready` on the policy verdict alone**. It waits for every gating lane and never for an advisory one, so a repo without the Claude reviewer no longer stalls; the release skill reads `lanes.*.body` and re-polls for a late Claude body.
- **The Claude lane's promotion points at ADR 0014** in `rules/review-severity.md`.
