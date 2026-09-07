# Copilot code review — scope

This repo runs a separate policy reviewer (the `.github/workflows/review-codex.yml` workflow —
the OpenAI Codex CLI on a ChatGPT subscription — guided by `AGENTS.md ## Review guidelines`,
which reviews every PR against the in-tree `rules/*.md`).
It owns conventions and policy. **Your job is the complementary lane: correctness and risk.**
Spend your review budget where the policy reviewer does not look.

## Review for
- Logic errors, wrong conditions, off-by-one, incorrect edge-case handling — especially in the
  shell under `skills/*/`, `hooks/`, and `scripts/`.
- Shell correctness: unquoted expansions, glob/word-splitting bugs, `set -euo pipefail` gaps,
  exit-code handling, `jq`/`gh` misuse, non-idempotent reruns.
- Resource leaks (unclosed files, leaked temp dirs), unbounded growth, needless recompute.
- Missing or wrong error handling that lets a real failure pass silently or crash — not
  stylistic preference.
- Security: injection, unsafe deserialization, secrets in code or logs, unvalidated untrusted input.
- Test coverage gaps: a changed branch or failure path with no test; an assertion that would pass
  even if the code were wrong.

## Do NOT comment on (the policy reviewer owns these)
- Naming/style conventions, formatting, import order.
- Commit-message or PR-title format, changelog entries, branch naming.
- Rule/policy compliance, rule/skill authoring conventions, prose style.
- Restating project rules — assume they are enforced by the policy reviewer.

## Repo facts (avoid false positives)
- Shell tests run under `scripts/run-tests.sh`, auto-discovering `**/tests/test_*.sh` —
  hermetic, no network. Scripts are invoked via `bash <path>` because tessl packaging strips
  the exec bit; do not flag a missing `+x` or suggest `[[ -x ]]` guards.
- The reviewer prompt, schema, and post/mask/leak-guard scripts live under
  `skills/onboard-repo/templates/` — one source shipped to consumers AND used by this repo's
  own `review-codex.yml`. Do not suggest duplicating them into `.github/`.
- Verify a `jq`/shell claim by the tool's real semantics before flagging — prefer a missed
  bug over a confident false positive.
