---
alwaysApply: false
applyTo: ".tessl-plugin/plugin.json, rules/**, skills/**, hooks/**, .tesslignore, CHANGELOG.md, README.md — when authoring or modifying plugin artifacts"
description: Plugin structure, rule format and frontmatter, prose discipline, surface sync, manual versioning, consistency audits — the authoring contract for this plugin
---

# Context Artifacts

## Plugin Structure

- The plugin has a `.tessl-plugin/plugin.json` manifest with `name`, `version`, and `description` — full schema in `rules/skill-authoring.md`
- The plugin's `README.md` is the project's `README.md` — same file, carrying the rules table, skills table, hooks table, and installation instructions
- Include the Tessl registry badge at the top of README: `[![tessl](https://img.shields.io/endpoint?url=https%3A%2F%2Fapi.tessl.io%2Fv1%2Fbadges%2Fgamussa%2Fcoding-policy)](https://tessl.io/registry/gamussa/coding-policy)`
- Rules live in `rules/<name>.md`, skills in `skills/<name>/SKILL.md`, hooks in `hooks/<name>.sh`
- Use `.tesslignore` to exclude CI files, `scripts/`, and `docs/` from the published plugin
- Validate structure with `tessl plugin lint` before every publish; `CHANGELOG.md` and similar repo files show as orphaned in lint, which only tracks manifest-declared paths

## Rules Are Prose

- One concept per rule file — don't combine unrelated concerns
- Compose by reference (`see rules/foo.md`), don't duplicate content across rules — if you want to state the same point in two rules, one states it and the other references it
- H1 title matching the filename concept (e.g., `# Commit Conventions` for `commit-conventions.md`)
- No code blocks unless demonstrating a specific command

## Rule Frontmatter

- Tessl preserves rule frontmatter byte-identical from publish to install; the consuming agent reads it and narrows when to act
- Always-on rules: `alwaysApply: true`, no `applyTo:`; `description:` is optional
- Conditional rules: `alwaysApply: false` plus `applyTo: "<glob list> — <natural-language clause>"` — both halves required, the em dash separates file scope from action scope
- `description:` is a summary, never a substitute for `applyTo:` on a conditional rule
- Path-scope only when **every** prescription in the rule is bound to a specific file set; a rule mixing file-bound and broad guidance stays always-on — a too-narrow scope drops the broad bits

## Writing Style

- Applies to auto-loaded artifacts: rules, `SKILL.md`, README; out of scope for files the agent opens only by choice (`references/**`, lookup tables) and for CHANGELOG
- Cut logical connectives that introduce justification — therefore, however, because, since, thus, consequently, moreover, although, despite, as a result, in order to — strip the clause and move the explanation to CHANGELOG; em-dash, colon, and semicolon are not loopholes for the same clause
- Cut noise intensifiers (very, extremely, quite, really, fully, exactly, completely) unless carrying a real constraint (`sum to exactly 100`)
- Cut meta-justification, reference incidents, worked examples, and cross-rule rationale — CHANGELOG is the archive for all of it
- Keep file paths, command names, flag literals, version numbers, env vars, error codes, and constraint words (required, optional, mandatory, forbidden)
- Keep carve-out preconditions in full
- Atomic bullets — one directive per bullet; anti-patterns as bullets ("X does NOT qualify"), never buried in prose
- Carve-outs: lead with "Narrow exception for X.", then numbered preconditions, then a one-line reset stating every other case follows the rule; never comma-splice preconditions
- 3–6 H2 sections per rule, ~25–45 lines; a rule covering one coherent policy area with several sub-aspects may run larger
- At most one parenthetical clause per sentence; active voice, present tense
- Lists naming forbidden terms themselves (like this section's own bullets) are not violations
- A CHANGELOG entry runs as long as its archive role needs — the only bounds are no duplication across entries and no verbatim restatement of the PR body

## Surface Sync

When you add, remove, or rename a rule, skill, or hook, update **all** of these:

- `.tessl-plugin/plugin.json` — the `rules`, `skills`, or `hooks` entry
- `README.md` — the rules, skills, or hooks table
- `CHANGELOG.md` — an entry describing the change
- `.claude/CLAUDE.md` — the `@../rules/<name>.md` import list

## Versioning and CHANGELOG

- Every PR that changes shipped content (`rules/`, `skills/`, `hooks/`, the manifest) bumps `.tessl-plugin/plugin.json` `version` by hand, per semver — patch by default, minor for a new rule/skill/hook, major for a removed or renamed one
- The same PR writes the `## <version> — <date>` heading above its CHANGELOG entries by hand; no auto-stamp step exists
- `scripts/check-version-bump.sh` fails CI when the manifest version is not strictly greater than the registry's latest
- `.github/workflows/publish.yml` publishes the manifest version as-is on push to `main`; a version already in the registry reds the publish
- No `Unreleased` section — the heading is forbidden
- Consolidate: multiple PRs reworking the same rule become one entry with the final outcome; audit the top section for duplication

## Consistency Check

After modifying rules, audit for cross-rule alignment:

- No duplicated bullets across rules — if two rules say the same thing, one should reference the other
- Don't duplicate long command literals or contract statements between rules and skills — rules state the contract, skills or their scripts carry the executable form per `rules/script-delegation.md`
- New rules don't contradict existing ones
- Skills follow the conventions their own rules prescribe
- Documentation tables match `.tessl-plugin/plugin.json` entries

## Post-Edit Rule Audit

After editing a rule, audit the repo itself against the new rule text and fix any drift in the same PR:

- Grep for every instance of the pattern the rule governs (`.env.example` files, `SKILL.md` step headings, hook markers) and update them to satisfy the new wording
- A rule that doesn't describe what's already committed in the repo erodes trust in every rule
- If drift can't be fixed in the same PR, file a follow-up issue that references the rule-edit commit
