# Changelog fragments

A PR that changes shipped content (`rules/`, `skills/`, `hooks/`, `.tessl-plugin/`, `.codex-plugin/`) adds one or more fragment files here instead of bumping the version or writing a `CHANGELOG.md` heading. The publish workflow assigns the version on merge.

## Format

One file per entry, `changelog.d/<name>.md`. `<name>` uses letters, digits, `.`, `_` and `-`, and starts with a letter or digit. Pick a name that will not collide with another open PR, such as the branch name.

```markdown
---
bump: patch
---
### Fixed

- **What changed, in bold**. Why it changed and how it was proven.
```

- The front matter holds only `bump`, one of `patch`, `minor` or `major`.
- `patch` covers fixes and rule tightening, `minor` a new rule, skill or hook or a process change, and `major` a removed or contract-breaking one.
- The body has one or more `### <Section>` headings, each followed by at least one `- ` bullet. Nested bullets and continuation lines are kept as written.
- Use the usual sections: `Added`, `Changed`, `Deprecated`, `Removed`, `Fixed`, `Security`. Other names are allowed and sort after them alphabetically.
- No `#` or `##` headings, and no text before the first section.

`bash scripts/check-changelog-fragment.sh origin/main` checks the branch's committed fragments and fails a PR that edits a manifest `version` or adds a `## <version>` heading. CI runs the same check on every PR.

## On merge

`.github/workflows/publish.yml` runs `scripts/assemble-release.sh`:

1. The next version is the highest of the registry's latest and both manifests, bumped by the highest `bump` among the pending fragments.
2. Both manifests get that version.
3. `CHANGELOG.md` gets a `## <version> — <date>` heading above the previous release. The fragments' sections are merged by name below it, with entries in fragment file-name order.
4. The fragments are deleted. This README stays.
5. The workflow commits the result as `chore(release): <version>`, pushes it to `main` and publishes.

A merge that leaves no fragment here assigns no version; it publishes only a manifest version still ahead of the registry from an earlier failed publish.
