# Draft a Pull Request for a New Utility Function

## Problem Description

Your team's data pipeline library has been growing steadily and follows a strict engineering process for all code changes. A colleague has just finished a small but useful addition to the utility module — a personalised greeting helper — and pushed it to a feature branch called `feat/add-greeting` in the repository under `inputs/repo/`. The branch was pushed to the repository's `origin`, `https://github.com/acme-data/pipeline-utils.git`; the checkout under `inputs/repo/` is rebuilt without its remotes, so take the remote from here.

Before anything can be merged, the team requires a properly formatted pull request to be drafted. Your job is to inspect the diff, understand the change, and produce a ready-to-use PR draft — one that can be handed to whoever runs the `gh pr create` command.

The repository does not use a pull request template file, so the body structure is up to you — but the team expects the draft to be professional and ready to submit without any further editing.

## Output Specification

Produce a single file called `pr-draft.md` in your working directory containing:

1. The exact `gh pr create` command the team would run (with `--title` and `--body` fully spelled out, either inline or via heredoc). The command does not need to be executable — write it as it would appear in a shell script.
2. The PR title embedded in the command must follow the team's conventional-commit naming scheme.
3. The PR body embedded in the command must be complete and self-contained.

Do **not** run any git operations that would alter the repository. Read the repository state from `inputs/repo/` to inform your draft.
