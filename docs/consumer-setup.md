# Consumer setup

How a repo adopts `gamussa/coding-policy`. The `onboard-repo` skill automates every step below; this page is the human-readable map.

This plugin started as a fork-in-spirit of [jbaruch/coding-policy](https://github.com/jbaruch/coding-policy); the reviewer scripts, hooks, and release tooling descend from it.

## What lands in the consumer

| Path | Purpose |
| --- | --- |
| `tessl.json` | Declares `gamussa/coding-policy` at `latest`; `tessl install` resolves it into the gitignored `.tessl/` |
| `.gitignore` block | Ignores `.tessl/` and the per-agent files tessl generates; `AGENTS.md` / `CLAUDE.md` stay committed |
| `.github/workflows/review-codex.yml` | On every same-repo PR: checks out `gamussa/coding-policy@main` into `.coding-policy/`, runs the Codex CLI review against `.coding-policy/rules`, posts the verdict as `github-actions[bot]` |
| `.github/codex-review/` | The reviewer prompt, output schema, and the post / mask / leak-guard scripts (copied from this plugin's `skills/onboard-repo/templates/`) |
| `.github/copilot-instructions.md` | Scopes Copilot to the correctness lane |

## Secrets

One per consumer repo, set at `https://github.com/<owner>/<repo>/settings/secrets/actions`:

- `CODEX_AUTH_JSON` — the full contents of `~/.codex/auth.json` after `codex login` (Sign in with ChatGPT). No API key, no per-token billing. Re-seed when the review fails on expired auth.

No Tessl token is needed in a consumer: the policy is read from a public GitHub checkout, not from the Tessl registry, at review time.

## Verdict semantics

`post-review.sh` derives the review event from per-finding severity: any blocking finding → `REQUEST_CHANGES` (gates the merge); advisory-only → `COMMENT`; clean → `APPROVE`, which falls back to `COMMENT` because `github-actions[bot]` cannot approve. The release skill dismisses a superseded `CHANGES_REQUESTED` via `skills/release/dismiss-stale-reviews.sh` before merging.

## Upgrading

Re-run the skill in override mode. `scaffold.sh --override` overwrites the reviewer files from the current plugin templates and restores them if any write fails; `tessl update` refreshes the rules themselves (the `check-policy-freshness` hook reminds you once a day).
