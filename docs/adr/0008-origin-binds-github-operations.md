# Origin Binds Every GitHub Operation

## Status

accepted

## Date

2026-10-15

## Context

The release and onboarding flows push with git to `origin` but ran `gh pr create`, `gh pr merge`, `gh pr view`, `gh run watch`, `gh run view`, and `gh secret set` without `--repo`. Without `--repo`, gh picks its repository from `gh repo set-default`, or prompts or guesses when a checkout has several remotes. In a fork whose default is the upstream parent, a PR, a merge, or a secret could land in a repository the operator does not own, against `rules/external-repo-contributions.md`. A hostless `--repo <owner>/<repo>` is not enough either: gh resolves its host from `GH_HOST`, so an environment pointing at another server (a GitHub Enterprise host) would send the same call there, and onboarding could upload `CODEX_AUTH_JSON` to it. `gh help environment` documents `GH_HOST`; `--repo` accepts `[HOST/]OWNER/REPO`, and `gh api` takes `--hostname`. With `GH_HOST` set to an unreachable host, `gh repo view cli/cli` and `gh api repos/cli/cli` fail connecting to that host, while `gh repo view github.com/cli/cli`, `gh run list --repo github.com/cli/cli`, and `gh api --hostname github.com repos/cli/cli` reach github.com (gh 2.100.0). `skills/release/verify-publish-landed.sh` read the publish run the same way. jbaruch/coding-policy closed the same gap in `13bf9a2` and `bd1d356`.

Options considered:

- **Rely on gh's default repository.** No change, but the target is whatever `gh repo set-default` last recorded, invisible in the command and wrong in a fork.
- **Pass `--repo` from the skill's `<owner> <repo>` inputs.** Explicit, but nothing says where the values come from; an agent filling them with `gh repo view` reads the same default it was meant to avoid.
- **Resolve the repository from `origin`.** git already pushes to `origin`, so the gh target and the git target are the same repository by construction. Parsing a remote URL is deterministic and fits a script.
- **Pin the host per call, or per process with `GH_HOST=github.com`.** A per-call pin (`--repo github.com/<owner>/<repo>`, `gh api --hostname github.com`) is visible in every command and printed hint an operator copies; a process-wide override is invisible in the hint and lost when the operator reruns the command by hand.

## Decision

`origin` is the authority for every GitHub operation in the release and onboarding flows.

- Every GitHub operation targets `github.com`, the only host `origin-repo.sh` accepts, pinned per call: `--repo github.com/<owner>/<repo>` on every repository-scoped command, `--hostname github.com` on every `gh api` and `gh auth status` call.
- `skills/release/origin-repo.sh` prints origin's GitHub repository as `{"repo", "owner", "name", "host", "target"}`, where `target` is the host-qualified `github.com/<owner>/<repo>`. It refuses an origin that is not a GitHub repository URL, and a push URL naming a different repository than the fetch URL. It never prints the remote URL or suggests a command that does, since the URL can carry credentials.
- `skills/release/SKILL.md` Step 1 resolves `<owner>/<repo>` with that script, and every gh command the release skill, the onboard skill, the reviewer workflow and its consumer template, the example environment file, and `rules/ci-safety.md` show carries `--repo github.com/<owner>/<repo>`. `gh repo set-default` is never consulted.
- Calls whose repository comes from another authority are pinned to the host the same way: the reviewer's `post-review.sh` posts to the workflow's own `github.repository`, the onboard preflight checks `gh auth status --hostname github.com`, and the Herdr `verify-authority.sh` reads the slug its brief names.
- Scripts that call gh take `<owner> <repo>` arguments and pin every call and every recovery hint they print to `github.com`. `verify-publish-landed.sh` changes from `<workspace> <plugin> <pre-baseline> <run-id>` to `<workspace> <plugin> <pre-baseline> <run-id> <owner> <repo>`; the four-argument call exits 2.

## Consequences

- A fork's `gh repo set-default` can never be targeted: the flows act on the repository the branch was pushed to, and an operator who wants another target changes `origin` deliberately.
- `GH_HOST` can never redirect a call, so a credential or a PR never reaches another server. The plugin's GitHub flows are github.com-only; supporting a GitHub Enterprise origin would need a new decision that resolves the host from `origin` too.
- An origin that is not on GitHub, or whose push and fetch URLs disagree, stops the release at Step 1 instead of guessing.
- Callers of `verify-publish-landed.sh` must pass the two new arguments; the scenario-5 eval criteria follow the new call.
- Later work must honor this contract: a new gh call in a shipped skill, script, template, or diagnostic names its host — `--repo github.com/<owner>/<repo>` from `origin-repo.sh` or from `<owner> <repo>` the caller resolved with it, or `--hostname github.com` — never gh's default repository or a hostless target. The release suites run their gh mocks behind `skills/release/tests/gh_pin_gate.sh` with `GH_HOST` set elsewhere.
