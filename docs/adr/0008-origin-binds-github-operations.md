# Origin Binds Every GitHub Operation

## Status

accepted

## Date

2026-10-15

## Context

The release and onboarding flows push with git to `origin` but ran `gh pr create`, `gh pr merge`, `gh pr view`, `gh run watch`, `gh run view`, and `gh secret set` without `--repo`. Without `--repo`, gh picks its repository from `gh repo set-default`, or prompts or guesses when a checkout has several remotes. In a fork whose default is the upstream parent, a PR, a merge, or a secret could land in a repository the operator does not own, against `rules/external-repo-contributions.md`. `skills/release/verify-publish-landed.sh` read the publish run the same way. jbaruch/coding-policy closed the same gap in `13bf9a2` and `bd1d356`.

Options considered:

- **Rely on gh's default repository.** No change, but the target is whatever `gh repo set-default` last recorded, invisible in the command and wrong in a fork.
- **Pass `--repo` from the skill's `<owner> <repo>` inputs.** Explicit, but nothing says where the values come from; an agent filling them with `gh repo view` reads the same default it was meant to avoid.
- **Resolve the repository from `origin`.** git already pushes to `origin`, so the gh target and the git target are the same repository by construction. Parsing a remote URL is deterministic and fits a script.

## Decision

`origin` is the authority for every GitHub operation in the release and onboarding flows.

- `skills/release/origin-repo.sh` prints origin's GitHub repository as `{"repo", "owner", "name"}`. It refuses an origin that is not a GitHub repository URL, and a push URL naming a different repository than the fetch URL. It never prints the remote URL or suggests a command that does, since the URL can carry credentials.
- `skills/release/SKILL.md` Step 1 resolves `<owner>/<repo>` with that script, and every gh command the release skill, the onboard skill, the reviewer workflow's instructions, and `rules/ci-safety.md` show carries `--repo <owner>/<repo>`. `gh repo set-default` is never consulted.
- Scripts that call gh take `<owner> <repo>` arguments and pass `--repo` on every call and in every recovery hint they print. `verify-publish-landed.sh` changes from `<workspace> <plugin> <pre-baseline> <run-id>` to `<workspace> <plugin> <pre-baseline> <run-id> <owner> <repo>`; the four-argument call exits 2.

## Consequences

- A fork's `gh repo set-default` can never be targeted: the flows act on the repository the branch was pushed to, and an operator who wants another target changes `origin` deliberately.
- An origin that is not on GitHub, or whose push and fetch URLs disagree, stops the release at Step 1 instead of guessing.
- Callers of `verify-publish-landed.sh` must pass the two new arguments; the scenario-5 eval criteria follow the new call.
- Later work must honor this contract: a new gh call in a shipped skill, script, or diagnostic carries `--repo` from `origin-repo.sh` or from `<owner> <repo>` arguments the caller resolved with it, never gh's default repository.
