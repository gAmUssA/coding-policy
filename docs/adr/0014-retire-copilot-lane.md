# Retire the Copilot Review Lane

## Status

accepted

## Date

2026-10-28

## Context

`docs/adr/0010-review-lanes.md` classified reviews into three lanes (`codex-policy`, `claude-quality`, `copilot`) and kept Copilot as an advisory second reviewer until the Claude code-quality reviewer (`.github/workflows/review-claude.yml`) had run green on several PRs. It has. Copilot still cost plumbing on every PR: a GraphQL review request after each push, a `review_unrequested` watcher result, a `requested` flag and inline-comment count of its own, and a `.github/copilot-instructions.md` scaffolded into every consumer.

Options considered:

- **Keep Copilot as a third advisory lane.** No code change, but every push still owes a request, and two advisory correctness reviewers duplicate each other.
- **Drop the lane but keep always-`none` snapshot fields.** Readers of the old shape keep parsing, but every reader then carries a field that can never change.
- **Remove the lane and its fields outright.** Every in-repo reader moves in the same change.

## Decision

This record supersedes 0010 and restates the decisions of it that still stand.

1. `skills/release/review-lanes.sh` owns the classifier. Each review is classified by login plus a marker at the very start of its body into `codex-policy` or `claude-quality`. A marker anywhere else in the body never reclassifies a review. A marker from a login no lane lists claims no lane.
2. Unmarked reviews from the Codex login classify as `codex-policy` by login fallback.
3. The Codex reviewer opens its body with `<!-- coding-policy:codex-policy-review:v1 -->`. The Claude reviewer opens its body with `<!-- coding-policy:claude-quality-review:v1 -->`.
4. Poll, watch and dismiss read verdicts per lane. `reviews.codex` is the `codex-policy` lane only. The snapshot's `lanes` view carries a `gating` flag per lane. Dismissal requires a later all-clear from the same lane.
5. Gating is a named constant per lane: `CODEX_POLICY_GATES=true`, `CLAUDE_QUALITY_GATES=false`. The watcher blocks on any gating lane's `CHANGES_REQUESTED` and waits for every gating lane to post. It waits for an advisory lane only when the lane is `expected`: the lane names a workflow (`CLAUDE_QUALITY_WORKFLOW`) and a check from that workflow ran on head. A repo without the Claude reviewer never waits for it.
6. The `copilot` lane is removed. A review from `copilot-pull-request-reviewer[bot]`, or any other login outside a lane, classifies as no lane: no lane gate reads it and it is never dismissed. The snapshot reports each such login's active verdict under `other_reviews` (with `is_bot`, not collapsed against head, resolved as GitHub does so a later `COMMENTED` never clears `CHANGES_REQUESTED`) and counts its top-level inline comments under `inline_comments.other`, so every body is still read. A human reviewer's `CHANGES_REQUESTED` there makes the watcher return `changes_requested`; a bot's never does.
7. The snapshot drops `reviews.copilot`, `lanes.copilot` and `inline_comments.copilot` with no compatibility stub. The watcher drops the `review_unrequested` result. `request-copilot-review.sh` is deleted.
8. `onboard-repo` stops scaffolding `.github/copilot-instructions.md`. A consumer's existing copy is neither a target nor removed.

## Consequences

- No review request is owed after a push; both lanes run on the `pull_request` events.
- A consumer repo that still enables Copilot keeps receiving its reviews; the release flow neither waits for nor gates on them, and reads every body through `other_reviews[].reviews[]`.
- In a repo running the Claude reviewer, the watcher reaches `ready` only after both the policy and the Claude verdicts post on head. In a repo without it, the policy verdict alone suffices.
- An external reader of the old snapshot shape that reads `.reviews.copilot` gets null. No such reader exists in this repo.
- Promotion of the Claude lane follows the path 0010 set: flip `CLAUDE_QUALITY_GATES` once its blocking findings prove accurate, give the lane its own GitHub App identity first, and change the `rules/review-severity.md` advisory bullet in the same PR. While it shares `github-actions[bot]`, the Claude lane posts `COMMENT` only.
- Inline comments are counted per login. Claude inline comments under the shared login count toward `inline_comments.codex`, which over-counts in the fail-safe direction.
