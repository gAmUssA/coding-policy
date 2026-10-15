#!/usr/bin/env bash
# Name the GitHub repository a checkout's `origin` remote points at.
#
# Every `gh` call in the release flow passes `--repo <owner>/<repo>` from this
# script, never gh's own default: without `--repo`, gh targets
# `gh repo set-default`, which in a fork can be the upstream parent — a PR,
# merge, or secret landing in a repository the operator does not own
# (rules/external-repo-contributions.md).
#
# Accepted origin URL forms, each with or without a trailing `.git` or `/`:
#   https://[<userinfo>@]github.com/<owner>/<repo>
#   ssh://[<user>@]github.com[:<port>]/<owner>/<repo>
#   [<user>@]github.com:<owner>/<repo>            (scp form)
# Every other scheme, host, or path shape is not a GitHub repository URL. The
# fetch URL and every push URL must name the same repository, compared
# case-insensitively as GitHub does; the output keeps the fetch URL's spelling.
#
# Usage: origin-repo.sh [checkout]
#   checkout  the git checkout to read; defaults to the current directory
# Out:   one JSON object on stdout — {"repo": "<owner>/<repo>", "owner": "...", "name": "..."}
#        stderr carries diagnostics only, never the remote URL (it can carry
#        credentials).
# Exit:  0 resolved; 1 origin is not a GitHub repository URL, or its fetch and
#        push URLs name different repositories; 2 setup error (bad usage, git
#        or jq absent, not a checkout, no origin remote).

set -euo pipefail

# The only host whose repositories gh is asked about.
readonly GITHUB_HOST="github.com"

# Set PARSED_OWNER / PARSED_NAME from one URL, or return 1.
parse_url() {
  local url="$1" host path
  if [[ "$url" =~ ^(https|ssh)://([^@/]*@)?([^/:@]+)(:[0-9]*)?/([^/].*)$ ]]; then
    host="${BASH_REMATCH[3]}"; path="${BASH_REMATCH[5]}"
  elif [[ "$url" =~ ^([^@/:]+@)?([^/:@]+):([^/].*)$ ]]; then
    host="${BASH_REMATCH[2]}"; path="${BASH_REMATCH[3]}"
  else
    return 1
  fi
  [[ "$(printf '%s' "$host" | tr '[:upper:]' '[:lower:]')" == "$GITHUB_HOST" ]] || return 1
  path="${path%/}"; path="${path%.git}"
  [[ "$path" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)/([A-Za-z0-9._-]+)$ ]] || return 1
  PARSED_OWNER="${BASH_REMATCH[1]}"; PARSED_NAME="${BASH_REMATCH[3]}"
  [[ "$PARSED_NAME" != "." && "$PARSED_NAME" != ".." ]]
}

lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

main() {
  if (( $# > 1 )); then
    echo "usage: origin-repo.sh [checkout]" >&2
    return 2
  fi
  local dir="${1:-.}"
  command -v git >/dev/null 2>&1 \
    || { echo "origin-repo: git not found on PATH — install git and rerun" >&2; return 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "origin-repo: jq not found on PATH — install it ('brew install jq' or 'apt install jq') and rerun" >&2; return 2; }
  if ! git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "origin-repo: ${dir} is not a git checkout — run from the repository or pass its path" >&2
    return 2
  fi

  local fetch_url push_urls
  if ! fetch_url=$(git -C "$dir" remote get-url origin 2>/dev/null); then
    echo "origin-repo: the checkout has no 'origin' remote — add it with 'git remote add origin <github-url>' and rerun" >&2
    return 2
  fi
  if ! push_urls=$(git -C "$dir" remote get-url --push --all origin 2>/dev/null); then
    echo "origin-repo: could not read origin's push URLs — run 'git remote -v' to inspect the remote" >&2
    return 2
  fi

  if ! parse_url "$fetch_url"; then
    echo "origin-repo: origin's fetch URL is not a GitHub repository URL — gh has no repository to target; run 'git remote -v' to inspect it" >&2
    return 1
  fi
  local owner="$PARSED_OWNER" name="$PARSED_NAME" url
  while IFS= read -r url; do
    [[ -n "$url" ]] || continue
    if ! parse_url "$url" \
       || [[ "$(lower "$PARSED_OWNER/$PARSED_NAME")" != "$(lower "$owner/$name")" ]]; then
      echo "origin-repo: an origin push URL names a different repository than its fetch URL (${owner}/${name}) — align them with 'git remote set-url --push origin <url>' and rerun" >&2
      return 1
    fi
  done <<<"$push_urls"

  jq -cn --arg o "$owner" --arg n "$name" '{repo: "\($o)/\($n)", owner: $o, name: $n}'
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
