#!/usr/bin/env bash
# Commit the files scripts/assemble-release.sh wrote and push them to the
# release branch. Run by .github/workflows/publish.yml after an assembly.
#
# Stages only the assembler's outputs — both manifests, CHANGELOG.md and the
# changelog.d/ deletions — so nothing else in the checkout can ride along.
# Commits as `chore(release): <version>` under the github-actions[bot]
# identity (RELEASE_GIT_NAME / RELEASE_GIT_EMAIL override it). On a rejected
# push (a merge landed after checkout), it rebases once onto the remote branch
# and pushes again. A rebase conflict aborts the rebase and fails: re-running
# the publish workflow re-assembles from the new tip, where the unconsumed
# fragments still sit.
#
# Usage: push-release.sh <version> [remote] [branch]
#   remote default origin; branch default main
# Out:   one JSON object on stdout:
#          {"pushed": true, "version": "x.y.z", "commit": "<sha>", "retried": bool}
# Exit:  0 pushed; 1 push rejected after the one retry, or the rebase
#        conflicted (rebase aborted, actionable stderr); 2 on usage error,
#        git missing, or nothing staged to commit.

set -euo pipefail

RELEASE_PATHS=(.tessl-plugin/plugin.json .codex-plugin/plugin.json CHANGELOG.md changelog.d)
BOT_NAME="github-actions[bot]"
BOT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"

main() {
  if [[ $# -lt 1 || $# -gt 3 ]]; then
    echo "usage: $0 <version> [remote] [branch]" >&2
    return 2
  fi
  local version="$1" remote="${2:-origin}" branch="${3:-main}"
  [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || { echo "error: version '${version}' is not numeric MAJOR.MINOR.PATCH — pass the .version that assemble-release.sh printed" >&2; return 2; }
  command -v git >/dev/null 2>&1 || { echo "error: git is not installed or not on PATH" >&2; return 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; return 2; }

  git add -A -- "${RELEASE_PATHS[@]}" \
    || { echo "error: git add of the release files failed" >&2; return 2; }
  if git diff --cached --quiet; then
    echo "error: nothing staged — run scripts/assemble-release.sh first; push-release.sh runs only after an assembly" >&2
    return 2
  fi
  # Author and committer both, through the environment: it outranks any
  # ambient git config, and the rebase below rewrites the commit as committer.
  export GIT_AUTHOR_NAME="${RELEASE_GIT_NAME:-$BOT_NAME}" GIT_AUTHOR_EMAIL="${RELEASE_GIT_EMAIL:-$BOT_EMAIL}"
  export GIT_COMMITTER_NAME="$GIT_AUTHOR_NAME" GIT_COMMITTER_EMAIL="$GIT_AUTHOR_EMAIL"
  git commit -q -m "chore(release): ${version}" \
    || { echo "error: git commit failed" >&2; return 2; }

  local retried=false
  if ! git push -q "$remote" "HEAD:${branch}"; then
    retried=true
    echo "push to ${remote}/${branch} rejected — rebasing once onto the new tip" >&2
    # --autostash: an unrelated dirty file in the runner checkout must not
    # block the rebase; it never enters the release commit.
    # git prints rebase progress on stdout; stdout carries only the JSON.
    if ! git pull -q --rebase --autostash "$remote" "$branch" >&2; then
      if ! git rebase --abort >&2; then
        echo "warning: git rebase --abort failed — the runner checkout is disposable, nothing to clean up by hand" >&2
      fi
      echo "error: rebasing the release commit onto ${remote}/${branch} conflicted — re-run the publish workflow; it re-assembles from the new tip" >&2
      return 1
    fi
    if ! git push -q "$remote" "HEAD:${branch}"; then
      echo "error: push to ${remote}/${branch} rejected again after the rebase — re-run the publish workflow" >&2
      return 1
    fi
  fi

  jq -n --arg v "$version" --arg c "$(git rev-parse HEAD)" --argjson r "$retried" \
    '{pushed: true, version: $v, commit: $c, retried: $r}'
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
