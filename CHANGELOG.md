# Changelog

Every entry carries the motivation and worked examples stripped from the rule bodies
(`rules/context-artifacts.md` Writing Style). Authors write the `## <version> — <date>`
heading by hand in the same PR that bumps `.tessl-plugin/plugin.json`.

## 0.1.0 — 2026-09-07

### Added

- **Initial release.** A fork-in-spirit of [jbaruch/coding-policy](https://github.com/jbaruch/coding-policy), rebuilt for Viktor Gamov's agents. The language-agnostic code rules, the autonomous-shipping discipline, the session hooks, the Codex policy reviewer, and the release skill are ported; the Herdr multi-agent layer, the fleet reviewer App, the auto-bump publish pipeline, and the skill-review gate are not.
- **24 rules.** Ten code-quality rules (commits, sync-before-work, testing, error handling, dependencies, file hygiene, CI safety, secrets, formatting, language diagnostics), eleven agent-behavior rules (boy-scout, ship-on-green, response-clarity, worktree isolation, review severity, reviewer-feedback reading, external-repo default-deny, hook-action reporting, verify-before-done, environment-changes, demo-readiness), and three plugin-authoring rules (script delegation, skill authoring, context artifacts).
- **Stack defaults.** `testing-standards`, `code-formatting`, and `language-diagnostics` name the tools to reach for on JVM (Kotlin/Java), Swift, TypeScript, and Python projects that have none configured. An existing project's configured tool always wins.
- **Three new rules Baruch's set does not carry.** `verify-before-done`: "done" means a shown passing run, never "should work". `environment-changes`: no machine-level installs or new project dependencies without stating them first; CI-runner installs are exempt. `demo-readiness`: conference-talk and workshop repos run top to bottom from a fresh clone with one command, a documented reset, and an offline-capable default path. `commit-conventions` additionally forbids AI attribution trailers in commits and PRs.
- **Manual versioning instead of auto-bump.** Every PR that changes shipped content bumps the manifest version and writes the CHANGELOG heading itself; `scripts/check-version-bump.sh` fails the PR when the manifest is not ahead of the registry or the heading is missing. `publish.yml` publishes the manifest version as-is on merge. This trades Baruch's registry-aware auto-bump plus stamp step for one deterministic gate and no bot commits on `main`.
- **Hooks.** `check-git-sync`, `check-policy-freshness`, and `check-tessl-latest` at `SessionStart` (informative, `Session-start status — ` marker); `stop-handoff-hygiene` at `Stop` for Claude Code and Codex (blocks once on leftover merged branches, orphaned worktrees, or diagnostics findings in the changed set).
- **Skills.** `release` (PR, Codex policy review plus advisory Copilot, watch, merge, cleanup, publish confirmation) and `onboard-repo` (install the plugin at `latest`, scaffold the per-repo Codex reviewer workflow that reads rules from a checkout of this repo, set the `CODEX_AUTH_JSON` secret, open the PR).
