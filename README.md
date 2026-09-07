# gamussa/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fgamussa%2Fcoding-policy)](https://tessl.io/registry/gamussa/coding-policy)

Coding policy plugin for Viktor Gamov's AI agents. Language-agnostic code quality rules, autonomous shipping discipline, stack defaults for JVM, Swift, TypeScript, and Python, plus the hooks, reviewer, and release workflow that enforce them.

A fork-in-spirit of [jbaruch/coding-policy](https://github.com/jbaruch/coding-policy): the code rules, the ship-on-green stance, the session hooks, and the Codex policy reviewer are ported; the multi-agent team layer and the fleet publishing pipeline are not. See [CHANGELOG.md](CHANGELOG.md) for what was kept, dropped, and added.

## Installation

```
tessl install gamussa/coding-policy
```

To wire a repository fully (plugin at `latest`, per-repo Codex policy reviewer, Copilot lane, `.tessl/` hygiene), run the `onboard-repo` skill from that repository. See [docs/consumer-setup.md](docs/consumer-setup.md).

## What's Included

### Rules

| Category | Rule | Summary |
|----------|------|---------|
| Git | [commit-conventions](rules/commit-conventions.md) | Imperative mood, one change per commit, PR hygiene, no AI attribution trailers |
| Git | [sync-before-work](rules/sync-before-work.md) | Fetch and sync the local checkout to the remote default before reading, planning, or editing |
| Testing | [testing-standards](rules/testing-standards.md) | Outcome-based, deterministic, no binary fixtures; per-stack test conventions |
| Errors | [error-handling](rules/error-handling.md) | Specific exceptions, shell `set -euo pipefail`, actionable messages, structured logging |
| Deps | [dependency-management](rules/dependency-management.md) | Stdlib first, pinned versions with a renewal mechanism, lock files, no vendoring |
| Files | [file-hygiene](rules/file-hygiene.md) | Proper `.gitignore`, no generated files, entry-point guards, idempotent scripts |
| CI | [ci-safety](rules/ci-safety.md) | Never skip tests, never smuggle CI edits, always watch CI to a terminal state |
| Secrets | [no-secrets](rules/no-secrets.md) | No credentials in code; env vars or a secrets manager; `.env.example` |
| Style | [code-formatting](rules/code-formatting.md) | Use the project's formatter; stack defaults when none exists; no style-plus-logic commits |
| Types | [language-diagnostics](rules/language-diagnostics.md) | Enable the language engine, never silence a finding without cause, gate CI at zero |
| Discipline | [boy-scout](rules/boy-scout.md) | Leave it better than you found it; "pre-existing" is not a concept |
| Discipline | [ship-on-green](rules/ship-on-green.md) | Green gate is the approval; asking is deciding not to ship; three objective exits only |
| Discipline | [verify-before-done](rules/verify-before-done.md) | "Done" means a shown passing run; "should work" is not a state |
| Discipline | [environment-changes](rules/environment-changes.md) | State a machine-level install or a new dependency before making it |
| Communication | [response-clarity](rules/response-clarity.md) | Action first, numbered steps, plain errors, one next step, no preamble |
| Concurrency | [agent-worktree-isolation](rules/agent-worktree-isolation.md) | Git worktrees for concurrent agent work; cleanup; read-only exception |
| Review | [review-severity](rules/review-severity.md) | Blocking gates the merge, advisory never does; Copilot is always advisory |
| Review | [reviewer-feedback-reading](rules/reviewer-feedback-reading.md) | Read every reviewer's body before declaring merge-ready, `COMMENTED` included |
| Scope | [external-repo-contributions](rules/external-repo-contributions.md) | Default deny on issues, PRs, comments, and reactions in repos the operator does not own |
| Automation | [hook-action-reporting](rules/hook-action-reporting.md) | Relay `Session-start status —` hook payloads once, then act on what they name |
| Demos | [demo-readiness](rules/demo-readiness.md) | Talk and workshop repos run from a fresh clone with one command and a documented reset |
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic work goes in scripts, reasoning stays in the LLM; skills cite a script's contract |
| Authoring | [skill-authoring](rules/skill-authoring.md) | `SKILL.md` structure, flat step numbering, typed `Skill()` calls, manifest reference |
| Authoring | [context-artifacts](rules/context-artifacts.md) | Plugin structure, rule format and frontmatter, writing style, surface sync, manual versioning |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | Ship a branch: readiness checks, PR, Codex policy review plus advisory Copilot review, watch to a verdict, address feedback, merge on green, cleanup, and publish confirmation for plugin repos. |
| [onboard-repo](skills/onboard-repo/SKILL.md) | Bootstrap a consumer repo: install the plugin at `latest`, gitignore `.tessl/`, scaffold the per-repo Codex reviewer workflow and Copilot instructions, set `CODEX_AUTH_JSON`, open the PR. |

### Hooks

| Hook | Event | Description |
|------|-------|-------------|
| [check-policy-freshness](hooks/check-policy-freshness.sh) | SessionStart | Warns (throttled once a day) when installed Tessl plugins are behind the registry. Informative only. |
| [check-git-sync](hooks/check-git-sync.sh) | SessionStart | Fetches origin (throttled once an hour per repo) and warns when the local default branch is behind or diverged. Informative only. |
| [check-tessl-latest](hooks/check-tessl-latest.sh) | SessionStart | Warns when `tessl.json` pins a `gamussa/*` dependency instead of `latest`. Informative only. |
| [stop-handoff-hygiene](hooks/stop-handoff-hygiene.sh) | Stop (Claude Code + Codex) | Blocks the handoff once on leftover merged branches, orphaned worktrees, or shellcheck/pyright findings in the changed set. A dirty tree is reported, not blocked. |

## Philosophy

- **Language-agnostic rules, stack-aware defaults.** The rules apply to any language. Where a tool choice matters (test runner, formatter, diagnostics engine), the rule names a default per stack and defers to whatever the project already uses.
- **Autonomous by default.** Green required checks are the approval. The agent merges, cleans up, and reports; it asks only on Red, No-undo, or Murky.
- **Proof over claims.** A task is done when the run is shown. Hooks and CI mechanize the rules that agents forget: sync at session start, hygiene at handoff, policy review on every PR.
- **One concern per rule.** Each file covers one topic so it can be read, cited, and overridden on its own.
- **Deterministic things are scripts.** Anything with fixed inputs and outputs is a tested script the skill calls; the LLM keeps the judgment calls.
- **Loaded by default, scoped by intent.** Universal rules are `alwaysApply: true`; rules that only fire in specific files declare `applyTo:` so the agent narrows when to act.

## Development

```
bash scripts/run-diagnostics.sh   # shellcheck + pyright at zero findings
bash scripts/run-tests.sh         # every **/tests/test_*.sh suite
tessl plugin lint                 # manifest and structure
```

Every PR that changes shipped content bumps `version` in `.tessl-plugin/plugin.json` and adds a `## <version> — <date>` heading to `CHANGELOG.md`; `scripts/check-version-bump.sh` enforces it in CI. Merging to `main` publishes that version.
