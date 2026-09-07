# PR Reviewer Mechanics

Reference for Step 4 of the `release` skill — how the two PR reviewers are wired.

## Policy reviewer — one deployment

`.github/workflows/review-codex.yml` runs the OpenAI Codex CLI authenticated by a **ChatGPT subscription** (the `CODEX_AUTH_JSON` secret — no API key) via `codex exec` with the prompt and schema under `skills/onboard-repo/templates/` (this repo) or `.github/codex-review/` (a consumer repo scaffolded by `onboard-repo`). Consumer repos check out `gAmUssA/coding-policy@main` into `.coding-policy/` and review against those rules; this repo reviews against its own `rules/`.

- **Trigger:** `pull_request` `opened` / `synchronize` / `reopened` — reviews on open and re-reviews each pushed commit. Fork PRs are skipped (no secret access).
- **Authorship:** submitted with the workflow's `GITHUB_TOKEN`, so the author is `github-actions[bot]` everywhere.
- **Verdict:** derived from per-finding severity by `post-review.sh` — any blocking finding posts `REQUEST_CHANGES`, advisory-only posts `COMMENT`, a clean pass posts `APPROVE`.
- **APPROVE limitation:** `github-actions[bot]` cannot `APPROVE` (GitHub returns HTTP 422), so a clean pass falls back to `COMMENT`. A later `COMMENT` never supersedes an earlier `CHANGES_REQUESTED` in GitHub's merge gate, so Step 7 dismisses the stale request via `skills/release/dismiss-stale-reviews.sh`.
- **Auth / cost:** the token is read only from the secret at runtime and deleted before the post step; Codex refreshes the access token in-memory. If the refresh token expires, the review fails loudly — re-seed with `gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json`.

## Copilot — second reviewer with a different lens

Copilot reads for correctness, bugs, security, and test-coverage gaps that no rule targets, scoped by `.github/copilot-instructions.md`. Request it via `skills/release/request-copilot-review.sh <owner> <repo> <pr>` (GraphQL, union mode, verifies the request landed). Copilot is always advisory (`rules/review-severity.md`): its comments must be read (`rules/reviewer-feedback-reading.md`) but never gate the merge.
