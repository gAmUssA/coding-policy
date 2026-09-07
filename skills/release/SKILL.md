---
name: release
description: >
  Structured workflow for shipping code via GitHub pull requests: readiness checks,
  PR creation, version + CHANGELOG bump for plugin repos, dual-lens automated review
  (the Codex policy reviewer for rule compliance + Copilot for correctness), watching
  the PR to a terminal verdict, addressing feedback, merging on green without asking,
  and post-merge cleanup and publish verification. Use when the user wants to open a
  pull request, ship code, release, merge a branch, or handle post-merge cleanup.
---

# Release Skill

Process steps in order. Do not skip ahead.

Runs end-to-end from `git push` through merge + cleanup verification in one agent session. Do not stop between steps and do not ask whether to open the PR or merge — the green gates are the approval (`rules/ship-on-green.md`).

## Step 1 — Verify Readiness

- Confirm you are on a feature branch (not `main`/`master`)
- Run the test suite — all tests must pass (`bash scripts/run-tests.sh` in this plugin repo; the project's own runner elsewhere)
- Run the linter and the language-diagnostics gate — zero findings (`bash scripts/run-diagnostics.sh` here; the project's `tsc --noEmit` / `./gradlew check` / `swiftlint` / `ruff` + `pyright` elsewhere, per `rules/language-diagnostics.md`)
- Self-audit the diff against every rule whose domain covers the touched paths — the policy reviewer is a backstop, not the first read
- `bash -n <script>` must exit 0 on every changed shell script, and its own test suite must pass
- Plugin repos only: run `bash scripts/check-version-bump.sh` when shipped content changed (rules, skills, hooks, manifest); a red result means Step 3 is still owed
- If anything fails, fix it before proceeding

## Step 2 — Create PR

- Push the branch: `git push -u origin <branch>`
- Create the PR with `gh pr create`:
  - **Title**: `<type>(<scope>): <imperative summary>`
  - **Body**:
    ```
    ## Summary
    <what changed and why — 1-3 bullet points>

    ## Test plan
    - [ ] <verification steps, with the command that shows each one passing>
    ```
- No AI attribution trailers in the commit or PR body (`rules/commit-conventions.md`)

Proceed immediately to Step 3.

## Step 3 — Bump Version and CHANGELOG (plugin repos)

Applies to repos that publish a Tessl plugin (a `.tessl-plugin/plugin.json` exists). Otherwise proceed immediately to Step 4.

- Decide the bump per semver: patch for fixes and rule tightening, minor for a new rule/skill/hook, major for a removed or contract-breaking one
- Set `version` in `.tessl-plugin/plugin.json` and add a `## <version> — <YYYY-MM-DD>` heading above this release's CHANGELOG entries (`rules/context-artifacts.md` Versioning and CHANGELOG)
- `bash scripts/check-version-bump.sh` must exit 0 — CI runs the same gate on the PR; the publish workflow publishes the manifest version as-is, so a missed bump reds the publish after merge
- Commit and push the bump on the same branch

Proceed immediately to Step 4.

## Step 4 — Reviews Fire

Opening the PR, or pushing further commits, automatically triggers the policy reviewer (`.github/workflows/review-codex.yml`), which reviews the diff against the rules and posts a verdict as `github-actions[bot]`. Fork PRs are skipped (no secret access). Mechanics and the APPROVE limitation:

```text
skills/release/REVIEW_DETAILS.md
```

**Also request Copilot.** Copilot is the complementary lane — correctness, bugs, security, test gaps. The policy reviewer gates the merge only on **blocking** findings; Copilot is always advisory (read it, never gate on it) — `rules/review-severity.md`:

```bash
skills/release/request-copilot-review.sh <owner> <repo> <pr-number>
```

Proceed immediately to Step 5.

## Step 5 — Watch PR State to a Terminal Verdict

Block until the PR reaches a merge-gate-relevant terminal state. The watcher polls at a script-owned interval up to a script-owned budget and watches exactly the fields Step 7 reads — each bot's latest review state resolved against the head SHA, CI status, merge state. Never hand-roll a poll loop or wrap the watch in an invented `timeout` (`rules/ci-safety.md` Always Watch CI):

```bash
skills/release/watch-pr-reviews.sh <owner> <repo> <pr-number>
```

It returns the full snapshot plus `"watch": {"result": ..., "attempts": N, "elapsed_seconds": N}`. Branch on `.watch.result`:

- `ready` (exit 0) — mergeable, CI `success`/`none`, both bots posted, the policy reviewer not `CHANGES_REQUESTED`. Read every non-empty `reviews.*.body` (a `COMMENTED` verdict with zero inline comments still carries a body — `rules/reviewer-feedback-reading.md`), then proceed to Step 6.
- `changes_requested` (exit 0) — the policy reviewer found a blocking finding. Step 6, push, re-run the watcher.
- `ci_failure` (exit 0) — a check failed. Fix it (Step 6), push, re-run the watcher.
- `dirty` (exit 0) — the branch conflicts with the base and GitHub skipped the `pull_request:` workflows. Rebase, resolve, force-push, re-run the watcher.
- `pending_at_budget` (exit 1) — a signal never arrived. Inspect which field is still `none`/`pending` in the snapshot. If the policy reviewer never posted, check `gh run list --workflow review-codex.yml` — a missing or expired `CODEX_AUTH_JSON` secret is the usual cause (`gh secret set CODEX_AUTH_JSON < ~/.codex/auth.json`). Re-run the watcher once the cause is understood.

## Step 6 — Address Feedback

- **Read every review in full first** — each `reviews.*.body` and every inline comment, before judging any item (`rules/reviewer-feedback-reading.md`)
- **Then act by severity** (`rules/review-severity.md`): blocking — fix now; advisory — acknowledge, fold in only when a blocking round is already happening, else defer to a follow-up. Never burn a dedicated re-review round on a lone advisory
- **CI failures**: fix every one
- **Reply on EVERY thread** with these exact opening literals:
  - Accepted: `Fixed in <sha>`
  - Declined: `Declining — <reason with cited evidence>`
  - Advisory deferred: `Acknowledged — deferred to <follow-up ref>`
- Push fixes to the same branch; the policy reviewer re-runs automatically
- **Re-request Copilot after every push** via `skills/release/request-copilot-review.sh` — it does not re-post on its own
- Repeat Step 5 until the policy reviewer carries no blocking finding and every thread has a reply

## Step 7 — Merge + Cleanup

Only proceed when Step 5 returned `ready`, every non-empty review body has been read, and every inline comment has a reply per Step 6. Then merge automatically per `rules/ship-on-green.md` — do not pause to ask.

**Clear superseded review gates first.** `github-actions[bot]` cannot `APPROVE` (HTTP 422), so a clean re-review lands as `COMMENT` and does not supersede its own earlier `CHANGES_REQUESTED`; the stale request keeps the merge `BLOCKED`. Dismiss it (idempotent, JSON summary, decision predicate in the script header):

```bash
skills/release/dismiss-stale-reviews.sh <owner> <repo> <pr-number>
```

**Plugin repos: capture the registry baseline before merging** so the post-merge check has something to compare against:

```bash
PRE=$(skills/release/capture-registry-baseline.sh <workspace> <plugin> | jq -r .version)
```

**(A) From the base checkout:**

```bash
gh pr merge <N> --merge --delete-branch
git checkout main && git pull --ff-only
git branch -d <branch>
git remote prune origin
```

**(B) From an additional worktree** (`rules/agent-worktree-isolation.md`) — order is mandatory, `git branch -d` refuses a branch checked out anywhere:

```bash
gh pr merge <N> --merge --delete-branch
cd <path-to-base-checkout>
git checkout main && git pull --ff-only
git worktree remove <path-to-worktree>
git branch -d <branch>
git remote prune origin
```

**After merge — ordinary repos:** confirm `main` advanced (`git log -1 --oneline` shows the merge) and watch CI on `main` to green (`gh run watch`). Report the merged PR URL. Finish here.

**After merge — plugin repos:** bind to the merge commit, never "latest on main":

```bash
merge_sha=$(gh pr view <N> --json mergeCommit --jq '.mergeCommit.oid')
run_id=$(skills/release/resolve-publish-run.sh <owner> <repo> "$merge_sha" "Publish Plugin" | jq -r '.database_id')
gh run watch "$run_id"
landed=$(skills/release/verify-publish-landed.sh <workspace> <plugin> "$PRE" "$run_id") \
  || { echo "Publish not confirmed — $(jq -r '.reason // "see stderr"' <<<"$landed")" >&2; exit 1; }
CURRENT=$(jq -r '.current' <<<"$landed")
skills/release/verify-moderation-cleared.sh <workspace> <plugin> "$CURRENT"
```

`verify-publish-landed.sh` exits 0 only when the run concluded `success` AND the registry advanced past `PRE`; `verify-moderation-cleared.sh` exits 0 only when the published version is installable (exit 1 = blocked or still pending at budget — an unconfirmed release, never reported as success). Report the merged PR URL, the version published, and the registry + moderation confirmation. Finish here — the skill is complete.
