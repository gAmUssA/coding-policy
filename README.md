# gamussa/coding-policy

[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fgamussa%2Fcoding-policy)](https://tessl.io/registry/gamussa/coding-policy)

Coding policy plugin for Viktor Gamov's AI agents. Language-agnostic code quality rules, autonomous shipping discipline, stack defaults for JVM, Swift, TypeScript, and Python, plus the hooks, reviewer, and release workflow that enforce them.

A fork-in-spirit of [jbaruch/coding-policy](https://github.com/jbaruch/coding-policy): the code rules, the ship-on-green stance, the session hooks, the Codex policy reviewer, and the [Herdr](https://herdr.dev) multi-agent team layer are ported; the fleet reviewer App and the auto-bump publishing pipeline are not; versions are assigned on merge from changelog fragments instead. See [CHANGELOG.md](CHANGELOG.md) for what was kept, dropped, and added.

## Installation

### Tessl

```
tessl install gamussa/coding-policy
```

To wire a repository fully (plugin at `latest`, per-repo Codex policy reviewer, Copilot lane, `.tessl/` hygiene), run the `onboard-repo` skill from that repository. See [docs/consumer-setup.md](docs/consumer-setup.md).

### Codex personal marketplace

Use the `plugin-creator` skill to register this repository as `gamussa-coding-policy` in your personal marketplace and install it. Preserve existing marketplace entries and source files. The Codex manifest is [`.codex-plugin/plugin.json`](.codex-plugin/plugin.json).

Review the plugin hooks with `/hooks` in Codex, then start a new thread. Choose Tessl or the Codex plugin for a given Codex environment; enabling both runs both sets of hooks. See [docs/codex-setup.md](docs/codex-setup.md) for installation, path handling, updates, and verification.

## What's Included

### Rules

| Category | Rule | Summary |
|----------|------|---------|
| Git | [commit-conventions](rules/commit-conventions.md) | Imperative mood, one change per commit, PR hygiene, no AI attribution trailers |
| Git | [sync-before-work](rules/sync-before-work.md) | Fetch and sync the local checkout to the remote default before reading, planning, or editing |
| Testing | [testing-standards](rules/testing-standards.md) | Outcome-based, deterministic, no binary fixtures; per-stack test conventions |
| Errors | [error-handling](rules/error-handling.md) | Specific exceptions, shell `set -euo pipefail`, actionable messages, structured logging |
| Deps | [dependency-management](rules/dependency-management.md) | Stdlib first, uv for Python execution, documented bootstrap/hook exceptions, pinned versions, lock files, no vendoring |
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
| Teamwork | [agent-team-operation](rules/agent-team-operation.md) | Herdr teams: the standalone/team mode switch and the worker essentials (one writer per worktree, no git on the shared checkout, report files as the worker channel). The lead's full contract — staffing, specialist triggers, judge seat, tiers, fix loops, dispatch safety, task ledger, stall detection, review partitions, retrospectives, internal review before the PR — lives in [`skills/herdr-teamlead/references/team-operation.md`](skills/herdr-teamlead/references/team-operation.md), read by the lead before each round |
| Review | [review-severity](rules/review-severity.md) | Blocking gates the merge, advisory never does; Copilot is always advisory |
| Review | [reviewer-feedback-reading](rules/reviewer-feedback-reading.md) | Read every reviewer's body before declaring merge-ready, `COMMENTED` included |
| Scope | [external-repo-contributions](rules/external-repo-contributions.md) | Default deny on issues, PRs, comments, and reactions in repos the operator does not own |
| Automation | [hook-action-reporting](rules/hook-action-reporting.md) | Relay `Session-start status —` hook payloads once, then act on what they name |
| Demos | [demo-readiness](rules/demo-readiness.md) | Talk and workshop repos run from a fresh clone with one command and a documented reset |
| Tracking | [work-tracking](rules/work-tracking.md) | In a repo with `.beans.yml`, every task is a bean: found or created before the work, checked off during it, completed with a summary, committed with the code |
| Scope | [cross-boundary-changes](rules/cross-boundary-changes.md) | Map producer, consumers, contract, and verification per boundary before editing; decompose a multi-component task and build one component at a time; staged migrations |
| Docs | [architecture-decisions](rules/architecture-decisions.md) | An architecture-altering change ships an ADR in `docs/adr/` in the same PR; records are immutable and superseded, never rewritten |
| Authoring | [script-delegation](rules/script-delegation.md) | Deterministic work goes in scripts, reasoning stays in the LLM; skills cite a script's contract |
| Authoring | [skill-authoring](rules/skill-authoring.md) | `SKILL.md` structure, flat step numbering, typed `Skill()` calls, manifest reference |
| Authoring | [context-artifacts](rules/context-artifacts.md) | Plugin structure, rule format and frontmatter, writing style, surface sync, manual versioning |
| Authoring | [stateful-artifacts](rules/stateful-artifacts.md) | Cross-invocation state: schema, owner skill, `schema_version`, hints-not-authority, migration |

### Skills

| Skill | Description |
|-------|-------------|
| [release](skills/release/SKILL.md) | Ship a branch: readiness checks, PR, Codex policy review plus advisory Copilot review, watch to a verdict, address feedback, merge on green, cleanup, and publish confirmation for plugin repos. |
| [onboard-repo](skills/onboard-repo/SKILL.md) | Bootstrap a consumer repo: install the plugin at `latest`, gitignore `.tessl/`, scaffold the per-repo Codex reviewer workflow and Copilot instructions, set `CODEX_AUTH_JSON`, open the PR. |
| [herdr-teamlead](skills/herdr-teamlead/SKILL.md) | Lead coding rounds in [Herdr](https://herdr.dev): measure headroom, select capable independent workers, compose briefs, provision worktrees, dispatch workers in verified YOLO mode, observe the fleet, and gate release on their reports. Consult UX, accessibility, investigation, architecture, security, performance and documentation specialists when the round's diff trips a trigger declared in `.herdr/triggers.json`. A persistent task ledger tracks acceptance independently of Herdr status; retrospectives, lead handoffs, and an attention queue survive worker transitions and lead resets. A pinned judge adjudicates disputes and diagnoses an exhausted fix loop into a bounded remedy; stalled workers and spent worktrees are detected from evidence and pruned. |
| [herdr-standup](skills/herdr-standup/SKILL.md) | Daily standup with the named Herdr agents: ask each idle worker for DONE / PLAN / BLOCKED / REPORT, never interrupt a working one, show unresolved user attention first, then render a fixed-width table plus a Markdown record. |

### Hooks

| Hook | Event | Description |
|------|-------|-------------|
| [check-policy-freshness](hooks/check-policy-freshness.sh) | SessionStart | Warns (throttled once a day) when installed Tessl plugins are behind the registry. Informative only. |
| [check-git-sync](hooks/check-git-sync.sh) | SessionStart | Fetches origin (throttled once an hour per repo, repo hooks off, time-bounded without `timeout`) and fast-forwards a clean checked-out default branch that is strictly behind; reports every other drift. A Herdr worker never fetches. Never blocks. |
| [check-tessl-latest](hooks/check-tessl-latest.sh) | SessionStart | Runs `tessl update --yes` in a consumer with `gamussa/*` dependencies and reports each one's version transition; flags a `gamussa/*` pin that is not `latest`. Never blocks. |
| [beans-prime](hooks/beans-prime.sh) | SessionStart | In a repo with `.beans.yml`, loads the `beans prime` usage guide as context; warns when the CLI is missing. Silent elsewhere. Informative only. |
| [herdr-team-status](hooks/herdr-team-status.sh) | SessionStart | Names the live Herdr team: each named worker, its kind, and its lifecycle state. Silent outside Herdr. Informative only. |
| [stop-handoff-hygiene](hooks/stop-handoff-hygiene.sh) | Stop (Claude Code + Codex) | Blocks the handoff once on leftover merged branches, spent worktrees (clean and holding nothing the default branch lacks, pushed or not), or shellcheck (warning and above) / pyright findings in the changed set (venv-aware). Dirty, unmerged, locked, and detached worktrees, a dirty tree, and shellcheck info/style notes are reported, not blocked. |
| [herdr-supervision-stop](hooks/herdr-supervision-stop.sh) | Stop (Claude Code + Codex) | Gates the exact bound Herdr lead while assignments or unhandled events remain; a saved pause or handoff covering every active assignment releases it. Reads local state only. |
| [codex-session-start](hooks/codex-session-start.py) | SessionStart (Codex plugin) | Identifies rule paths and scopes, maps co-shipped script paths, and translates every Tessl-declared SessionStart hook into native Codex context. Registered in [hooks/hooks.json](hooks/hooks.json). |

## Philosophy

- **Language-agnostic rules, stack-aware defaults.** The rules apply to any language. Where a tool choice matters (test runner, formatter, diagnostics engine), the rule names a default per stack and defers to whatever the project already uses.
- **Autonomous by default.** Green required checks are the approval. The agent merges, cleans up, and reports; it asks only on Red, No-undo, or Murky.
- **Proof over claims.** A task is done when the run is shown. Hooks and CI mechanize the rules that agents forget: sync at session start, hygiene at handoff, policy review on every PR.
- **One concern per rule.** Each file covers one topic so it can be read, cited, and overridden on its own.
- **Deterministic things are scripts.** Anything with fixed inputs and outputs is a tested script the skill calls; the LLM keeps the judgment calls.
- **Loaded by default, scoped by intent.** Universal rules are `alwaysApply: true`; rules that only fire in specific files declare `applyTo:` so the agent narrows when to act.

## Development

Python tooling requires Python 3.11+ and `uv`. The Python suites use the standard library; no dependency lock file is needed for these commands.

```
bash scripts/run-diagnostics.sh   # shellcheck + pyright at zero findings
bash scripts/check-rules-budget.sh      # rules/*.md total within its byte budget
bash scripts/check-conflict-markers.sh  # no leftover merge-conflict markers
uv run --no-project --no-python-downloads --python '>=3.11' bash scripts/run-tests.sh
tessl plugin lint                 # manifest and structure
```

The direct-Python exception covers `hooks/hooks.json` invoking `hooks/codex-session-start.py` and `hooks/herdr-supervision-stop.sh` invoking `teamlead.supervision_hook`. These standard-library-only runtime hooks run in consumer sessions before developer tooling is available. They use an existing Python 3.11+ interpreter; hook execution must not install tools or download Python.

Remaining entry-point migration, under `rules/dependency-management.md` Python Execution:

- `.github/workflows/tests.yml` and `.github/workflows/publish.yml` run `bash scripts/run-tests.sh` without installing `uv`; migrate them first, in a CI-scoped PR that installs a pinned `uv` and runs the documented `uv run` command
- `skills/herdr-teamlead/teamlead.sh` and `skills/herdr-teamlead/prune-worktrees.sh` launch `python3` directly; migrate them after the workflow migration lands

Every PR that changes shipped content adds a changelog fragment, `changelog.d/<name>.md`, carrying its semver `bump` and its CHANGELOG entry (format: [changelog.d/README.md](changelog.d/README.md)). A PR never edits a manifest `version` or adds a `## <version>` heading; `scripts/check-changelog-fragment.sh` enforces both in CI:

```
bash scripts/check-changelog-fragment.sh origin/main
```

On merge, `.github/workflows/publish.yml` runs `scripts/assemble-release.sh` to assign the version and write the CHANGELOG entry, commits it to `main` as `chore(release): <version>` through `scripts/push-release.sh`, and publishes. A merge with no fragment assigns no version and publishes only a version whose earlier publish failed. Re-running a publish run whose publish step failed publishes the version it already committed. The decision is recorded in [ADR 0003](docs/adr/0003-version-on-merge.md).

### Evals

`evals/` holds Tessl eval scenarios that compare an agent with this plugin against a baseline without it. Two scenarios hand the agent a git repository; their histories are stored as `git fast-export` streams listed in `evals/git-fixtures.tsv`. Rebuild them before a run:

```
bash scripts/eval-fixtures.sh import
tessl eval run .   # add --model <id> on a paid Tessl plan
```

After changing a fixture's history, run `bash scripts/eval-fixtures.sh export` and remove the fixture's `.git` before staging it, or git records the directory as an empty submodule link.
