# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired.

## Policy reviewer — one deployment

`.github/workflows/review-codex.yml` runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key) via `codex exec` with the prompt and schema under `skills/onboard-repo/templates/` (this repo) or `.github/codex-review/` (a consumer repo scaffolded by `onboard-repo`). Consumer repos check out `gamussa/coding-policy@main` into `.coding-policy/` and review against those rules; this repo reviews against its own `rules/`.

- **Trigger:** `pull_request` `opened` / `synchronize` / `reopened` — reviews on open and re-reviews each pushed commit. Fork PRs are skipped (no secret access).
- **Authorship:** submitted with the workflow's `GITHUB_TOKEN`, so the author is `github-actions[bot]` everywhere.
- **Lane marker:** `post-review.sh` opens the body with `<!-- coding-policy:codex-policy-review:v1 -->`, so `skills/release/review-lanes.sh` tells this lane from another reviewer posting as `github-actions[bot]`. Unmarked older reviews fall back to this lane by login (`docs/adr/0010-review-lanes.md`).
- **Verdict:** derived from per-finding severity by `post-review.sh` — any blocking finding posts `REQUEST_CHANGES`, advisory-only posts `COMMENT`, a clean pass posts `APPROVE`.
- **APPROVE limitation:** `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so a clean pass falls back to `COMMENT`. A later `COMMENT` never supersedes an earlier `CHANGES_REQUESTED` in GitHub's merge gate, so Step 7 dismisses the stale request via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the token is read only from the secret at runtime and deleted before the post step; Codex refreshes the access token in-memory. If the refresh token expires, the review fails loudly — re-seed with `gh secret set CODEX_AUTH_JSON --repo github.com/<owner>/<repo> < ~/.codex/auth.json` (the `target` from `skills/release/origin-repo.sh`).

## Claude — advisory code-quality lane

`.github/workflows/review-claude.yml` (this repo only; `onboard-repo` does not scaffold it) runs the Claude CLI on a Claude subscription token (`CLAUDE_CODE_OAUTH_TOKEN`) and reviews for correctness, bugs, security, and test-coverage gaps that no rule targets.

- **Trigger:** the same `pull_request` events as the policy reviewer; no review request is needed.
- **Lane marker:** the body opens with `<!-- coding-policy:claude-quality-review:v1 -->`, so `skills/release/review-lanes.sh` classifies it `claude-quality` although it posts as `github-actions[bot]`.
- **Verdict:** always `COMMENT` while the lane is advisory (`docs/adr/0014-retire-copilot-lane.md`). Its body must be read (`rules/reviewer-feedback-reading.md`); it never gates the merge.

Copilot is no longer requested. A Copilot review in a repo that still enables it belongs to no lane and never gates (`rules/review-severity.md`).
