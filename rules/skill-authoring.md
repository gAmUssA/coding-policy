---
alwaysApply: false
applyTo: "skills/**/SKILL.md, skills/**/*.md, .tessl-plugin/plugin.json — when authoring or modifying skills"
description: SKILL.md structure, frontmatter, execution-mode preamble, flat step numbering, typed Skill() calls, plugin.json reference
---

# Skill Authoring

## SKILL.md Frontmatter

- Required fields: `name`, `description` (include trigger phrases so the agent knows when to activate)
- Optional fields include `allowed-tools`, `disable-model-invocation`, `user-invocable` (set to `false` for background-knowledge skills the runtime loads as context but the user should never invoke directly), and `argument-hint`
- `argument-hint` is a Claude Code slash-command UI hint — a string naming the expected arguments, e.g. `[install|upgrade] [repo]`
- This list is not a closed allow-list — agents preserve unrecognized skill frontmatter and ignore fields they don't use
- The `description` field is your discovery surface — write it for the agent, not a human audience

## Title and Preamble

- Start the body with an `# H1` title naming the skill (e.g., `# Release Skill` for `skills/release/SKILL.md`)
- The first content line after the H1 declares the execution mode and prevents the agent from parallelizing or freelancing
- Sequential workflows (default — release, onboard, migrate): preamble `Process steps in order. Do not skip ahead.`
- Action routers (agent picks one of several alternatives by user intent): preamble `This skill is an action router — pick the step that matches the user's intent and execute only that step. Do not run other steps; do not parallelize.` List the available actions in the skill's `description` so the runtime can match intent

## Step Structure

- Use flat numbered headings with descriptive titles: `## Step 1 — Verify Readiness`, `## Step 2 — Create PR`
- The same flat-numbering format applies to both sequential workflows and action routers — in routers, "Step N" labels each alternative action
- No decimals, no sub-steps — flat numbering only
- When inserting a step, renumber all subsequent steps
- Each step is one action. The check is a title heuristic: if the step's title combines two verbs with "and" or "&" (e.g., "Build and Deploy"), split it. A step's body may list sub-tasks all serving one cohesive action
- Action-router preambles are exhaustive on chaining: if a step chains to another, say so explicitly. Default in routers: execute only the chosen step and finish
- Default handoff between steps is "continue immediately" — never "pause and wait for the user"
- State continuations explicitly ("Proceed immediately to Step N"); if a step can legitimately end the skill, say so ("Finish here")
- If a step can legitimately produce no output, say so explicitly: "If no issues found, proceed silently"

## Keep Skills Compact

- SKILL.md is the execution plan, not a reference manual
- Move detailed reference material (API docs, long examples, lookup tables) to separate files under `skills/<name>/references/` or a sibling `*.md`
- Reference them with typed code blocks and full relative paths

## Typed Calls and Script References

- Invoke other skills with typed `Skill(skill: "name")` calls, never prose references
- Never call `Skill()` on a rule (rules are auto-loaded, not invoked via Skill)
- Deterministic operations must be executable script files, not inline code blocks — see `rules/script-delegation.md`
- In rule prose, documentation, and skill cross-references, use repo-relative paths (`skills/<name>/<file>.<ext>`)
- In step bodies, use the path that resolves at the invocation site: repo-relative when the skill runs from a clone of this repo (`skills/release/poll-pr-reviews.sh`), plugin-mount path when the skill runs inside a consumer (`.tessl/plugins/gamussa/coding-policy/skills/onboard-repo/scaffold.sh`)
- Don't mix conventions inside one SKILL.md — if one step invokes via a mount path, every other script-invoking step must too
- Include the expected input/output contract in the step description

## plugin.json Manifest Reference

The manifest lives at `.tessl-plugin/plugin.json`.

- Required: `name` (`<workspace>/<plugin-name>`), `version` (semver string), `description` (one sentence)
- Optional: `private` (`true` prevents publishing; set `false` to publish), `skills` (directory path or array of skill directory paths), `rules` (directory path or array of rule-file paths), `repository`, `homepage`, `license`, `author`
- `hooks` — cross-agent hook declarations keyed by event (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `SessionStart`, `Stop`), each mapping to hook groups (`matcher` optional, `hooks` array of `{type:"command", command, args?}`); Tessl translates the consensus payload to each agent's native form
- `nativeHooks` — per-agent hook declarations keyed by agent id (`claude-code`, `codex`), same group shape, written straight to that agent's native config; required for a `Stop` hook that blocks
- `alwaysApply` and `applyTo:` are not manifest fields — rule scope lives in the rule file frontmatter per `rules/context-artifacts.md`
