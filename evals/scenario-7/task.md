# Unblock Florentine's First Team Round

## Background

Florentine is a plugin-based deployment automation tool. The team just started running multi-agent review rounds on it with the coding-policy Herdr team lead. The first round stopped at preflight: the lead reports that this repository has not declared its trigger surfaces, so it cannot classify a diff or decide which specialists a round needs.

Set the repository up so rounds can classify its changes. The working directory stands in for the repository root, and the repository's layout is in `inputs/repo-structure.txt`. What the team told you about it:

- Each plugin under `plugins/` is its own unit of ownership, and so is the shared `scripts/` directory.
- The CLI lives in the Python files under `cli/` and registers its commands and flags with argparse.
- `docs/` is the user-facing documentation.
- The only code that runs on untrusted pull requests is the GitHub Actions workflows.

## Output Specification

Create whatever the team lead reads to classify this repository's diffs, at the path it reads from, relative to the working directory.
