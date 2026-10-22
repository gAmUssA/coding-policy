# Review Lanes Classified by Body Marker

## Status

accepted

## Date

2026-10-22

## Context

Two reviewers post on every PR today: the Codex policy reviewer (`review-codex.yml`, gating on blocking findings) and Copilot (always advisory). The operator decided to replace Copilot with a Claude code-quality reviewer that runs on a Claude subscription token, like Codex runs on a ChatGPT subscription. The Claude lane reviews correctness, bugs and security. It starts advisory for a trial of about two weeks.

The hazard is the login. Codex submits with the workflow `GITHUB_TOKEN`, so its reviews are authored by `github-actions[bot]`. A Claude workflow using the same token posts under the same login. The release scripts read "the latest review by a login":

- `dismiss-stale-reviews.sh` would treat a clean Claude `COMMENT` as that bot's all-clear and dismiss a blocking Codex `CHANGES_REQUESTED`. That is a gate evasion.
- `poll-pr-reviews.sh` and `watch-pr-reviews.sh` would report the Claude verdict as `reviews.codex` and could reach `ready` over an open Codex block.

Options considered:

- **A dedicated GitHub App per reviewer.** Distinct logins separate the lanes by identity, and an App can `APPROVE`, which supersedes its own earlier `REQUEST_CHANGES` without a dismissal. It costs a one-time App setup and two more secrets per reviewer.
- **A lane marker in the review body.** Each reviewer opens its body with an HTML comment naming its lane. The scripts classify by login plus marker. No new identity or secret is needed.
- **Both.** Upstream `jbaruch/coding-policy` (#776) ships the marker classifier and gives the Claude reviewer its own App.

## Decision

1. `skills/release/review-lanes.sh` owns the classifier. Each review is classified by login plus a marker at the very start of its body into `codex-policy`, `claude-quality` or `copilot`. A marker anywhere else in the body never reclassifies a review. A marker from a login no lane lists claims no lane.
2. Unmarked reviews from the Codex login classify as `codex-policy` by login fallback, so every review posted before this change keeps gating.
3. The Codex reviewer's `post-review.sh` opens its body with `<!-- coding-policy:codex-policy-review:v1 -->`. The future Claude reviewer must open its body with `<!-- coding-policy:claude-quality-review:v1 -->`.
4. Poll, watch and dismiss read verdicts per lane. `reviews.codex` is the `codex-policy` lane only. The snapshot adds a `lanes` view with a `gating` flag per lane. Dismissal requires a later all-clear from the same lane.
5. Gating is a named constant per lane in `review-lanes.sh`: `CODEX_POLICY_GATES=true`, `CLAUDE_QUALITY_GATES=false`, `COPILOT_GATES=false`. The watcher blocks on any gating lane's `CHANGES_REQUESTED` and waits for every gating lane to post.
6. This change lands before the Claude workflow exists. It does not change today's behaviour.

## Consequences

- A clean Claude comment can no longer dismiss or mask a Codex block, even under the shared login.
- The Claude workflow (a separate, CI-scoped PR) must post the marker as the body's first line, with a test that its body classifies as `claude-quality`. An unmarked Claude review under the shared login would classify as Codex, so the marker is load-bearing.
- While it shares `github-actions[bot]`, the Claude lane must post `COMMENT` only. GitHub's own merge gate keys `CHANGES_REQUESTED` on the login and does not see lanes, so a Claude `REQUEST_CHANGES` would block the merge GitHub-side despite the lane being advisory.
- Promotion to gating: after the trial, flip `CLAUDE_QUALITY_GATES` once its blocking findings prove accurate. The measure is the share of Claude-blocking findings the operator fixes rather than declines. The promotion PR also changes the `rules/review-severity.md` advisory bullet.
- Before the Claude lane gates, it needs its own GitHub App identity. `github-actions[bot]` gets HTTP 422 on `APPROVE`, so a gating Claude lane under the shared login would leave its own stale `CHANGES_REQUESTED` blocking GitHub's gate, and the per-login gate would mix the two lanes. An App adds `CLAUDE_REVIEW_APP_ID` and `CLAUDE_REVIEW_APP_PRIVATE_KEY` and its login joins `CLAUDE_QUALITY_LOGINS`.
- Inline comments are still counted per login. The Codex reviewer posts none today. If the Claude lane posts inline comments under the shared login, they count toward `inline_comments.codex`. That over-counts, which is the fail-safe direction, and the Claude workflow PR should split the count by each comment's review.
- Retiring Copilot (its request plumbing and the `copilot` lane) is a later PR, once the Claude lane has run green on several PRs.
