---
alwaysApply: true
description: Ask before changing the developer's machine or a project's dependency set — installs and new dependencies are stated decisions, never side effects
---

# Environment Changes

## Machine-Level Installs

- Never run `brew install`, `npm install -g`, `pip install --user`, `cargo install`, `gem install`, `xcode-select`, or `softwareupdate` without stating the exact command and getting a yes
- Never edit shell rc files (`~/.zshrc`, `~/.bashrc`, `~/.profile`), `~/.gitconfig`, or other dotfiles — the user's dotfiles are theirs
- Never change global tool versions (`sdk default`, `nvm alias default`, `pyenv global`, `xcode-select --switch`)
- A missing tool is reported with the install command the user can run, not installed on their behalf
- Project-local, gitignored installs (`node_modules`, a `.venv`, Gradle wrapper downloads, SPM checkouts) are part of the build and need no consent

## New Project Dependencies

- Adding a library, plugin, or GitHub Action to a manifest is a stated decision, never a side effect of "making it work"
- The statement names the package, the version, and the stdlib alternative rejected (`rules/dependency-management.md` Stdlib First)
- In an autonomous session, a one-line statement in the response before the edit satisfies this rule
- A genuinely disputed choice — two candidate libraries, a heavy framework for a small need — gets one question, then proceeds with the answer
- Removing or upgrading a dependency follows the same statement form

## CI Runner Carve-Out

- Narrow exception for installs inside a CI job.
- Preconditions (all required):
  1. The install runs on the CI runner, never on the developer's machine
  2. The PR's title and body scope the work as a CI change, or the install is part of an existing job the PR already modifies in scope
  3. The installed version is pinned with a renewal mechanism per `rules/dependency-management.md` Freshness
- Every other install still follows Machine-Level Installs and New Project Dependencies above
