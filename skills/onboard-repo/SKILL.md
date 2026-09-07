---
name: onboard-repo
description: >
  Bootstrap a consumer repo onto gamussa/coding-policy: install the plugin at `latest`,
  gitignore tessl's generated artifacts, scaffold the Codex policy-review workflow plus
  the Copilot lane charter, set the CODEX_AUTH_JSON secret, then commit and ship via the
  release skill. Use when the user wants to add, install, enable, scaffold, set up, wire
  up, or enroll coding-policy / an automated policy review / a PR reviewer in a repo.
  Also use to upgrade or refresh the reviewer files in a repo that already has them
  (override mode: "upgrade", "update", "refresh", "--override").
---

# Onboard Repo Skill

Process steps in order. Do not skip ahead.

Two modes, chosen by the user's request:

- **install** (default) — no reviewer files exist yet.
- **upgrade** (`--override`) — refresh previously scaffolded reviewer files to the current plugin version. Pass `--override` to preflight and scaffold.

Scripts run from the plugin mount inside the consumer: `.tessl/plugins/gamussa/coding-policy/skills/onboard-repo/<script>`. Run Step 2 first when the mount is absent — the preflight needs the installed templates.

## Step 1 — Run Preflight Checks

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/onboard-repo/preflight.sh            # install
bash .tessl/plugins/gamussa/coding-policy/skills/onboard-repo/preflight.sh --override # upgrade
```

Checks git worktree, `origin`, GitHub CLI auth, the Codex ChatGPT credential at `~/.codex/auth.json`, the installed templates, and target-file state. Returns `{"ok": bool, "override": bool, "failures": [...], "warnings": [...]}`.

- **Exit 0** — proceed to Step 2.
- **Exit 1** — report each failure's `reason` verbatim and stop; every failure names its recovery command. A `templates-present` failure means the plugin is not installed yet: run Step 2's install, then re-run this step.
- `warnings` are informational; report them and continue.

## Step 2 — Install the Policy

```bash
tessl install gamussa/coding-policy
bash .tessl/plugins/gamussa/coding-policy/skills/onboard-repo/tessl-hygiene.sh
```

`tessl install` writes `tessl.json` and the resolved state under `.tessl/`. `tessl-hygiene.sh` sets every `gamussa/*` dependency to `"version": "latest"` (third-party pins untouched — `rules/dependency-management.md` Runtime-Managed Manifest Carve-Out) and appends the tessl-generated-artifacts block to `.gitignore`, keeping `AGENTS.md`/`CLAUDE.md`/`GEMINI.md` committed. Emits `{"tessl_json": ..., "gitignore": ...}`; idempotent. If Step 1 was skipped for a missing mount, run it now, then proceed to Step 3.

## Step 3 — Scaffold the Reviewer

```bash
bash .tessl/plugins/gamussa/coding-policy/skills/onboard-repo/scaffold.sh            # install
bash .tessl/plugins/gamussa/coding-policy/skills/onboard-repo/scaffold.sh --override # upgrade
```

Writes `.github/workflows/review-codex.yml`, `.github/copilot-instructions.md`, and `.github/codex-review/{prompt.md,schema.json,post-review.sh,mask-secrets.sh,assert-no-secret-leak.sh}` from the plugin's templates. Install mode refuses any pre-existing target; upgrade mode overwrites and restores every target if a write fails. Emits `{"state": "scaffolded|no-op", "files": [...]}`. Proceed immediately to Step 4.

## Step 4 — Set the Reviewer Secret

```bash
gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json
```

The workflow authenticates Codex with the ChatGPT subscription credential preflight verified. Nothing is committed; the secret lives only in the repo's Actions secrets. Also add a `CODEX_AUTH_JSON=` entry with a one-line purpose and the repo's `https://github.com/<owner>/<repo>/settings/secrets/actions` link to `.env.example` (create the file if absent — `rules/no-secrets.md`). Proceed immediately to Step 5.

## Step 5 — Branch, Commit, Ship

- Branch: `chore/onboard-coding-policy` (install) or `chore/upgrade-coding-policy` (upgrade), cut from the synced default (`rules/sync-before-work.md`)
- One commit staging `tessl.json`, `.gitignore`, `.env.example`, and the scaffolded `.github/` files: `ci(review): add gamussa/coding-policy PR review setup` (install) or `ci(review): upgrade gamussa/coding-policy PR review setup`
- Then invoke `Skill(skill: "release")` to open the PR, watch the reviews, and merge on green. The scaffolded reviewer runs on this very PR — its summary begins `Policy loaded: N rule files from .coding-policy/rules.`, which confirms the policy checkout worked

Finish here — the release skill completes the flow.
