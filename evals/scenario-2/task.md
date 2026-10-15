# Get the env-linting Skill Ready to Ship

## Background

The `acme/dev-toolkit` plugin is checked out in your current working directory, on the `feature/add-env-linting-skill` branch. A teammate's commit on that branch adds a new `env-linting` skill. Nobody has opened the PR yet.

You are picking the branch up to get it ready for release. Prepare it the way this team ships changes: record whatever this change needs recorded before the PR goes up, and commit that on the branch. There is no remote configured, so do not push or open the PR.

## Output Specification

- The branch, with your release preparation committed on it.
- `validation-result.txt` in the working directory, holding the full output of the check you ran to confirm the branch is ready to release.
