#!/usr/bin/env bash
# Shared test helpers for the release scripts' github.com host pin
# (docs/adr/0008-origin-binds-github-operations.md). Sourced by the suites;
# defines functions only.
#
# A hostless `--repo owner/name` or a bare `gh api` lets GH_HOST redirect the
# call to another server. Every suite that sources this file exports GH_HOST
# pointing elsewhere, so a call or a printed hint that is not pinned is the
# exact call that env would redirect.
#
#   gh_pin_gate <mock-fn> <args...>
#     Fails (return 97, stderr names the call) unless the call is pinned:
#     `api` must lead with `--hostname github.com`; every other call must
#     carry `--repo`/`-R` with a `github.com/` prefix. A pinned call is handed
#     to <mock-fn> with the pin stripped, so the mock sees the plain shape.
#   gh_hints_pinned <text>
#     Fails unless every gh command the text tells the operator to run
#     names github.com, and the text carries at least one such command.
#
# Sourcing applies `set -euo pipefail` to the sourcing shell; an aggregate
# harness that runs without `-e` restores it with `set +e` right after.

set -euo pipefail

export GH_HOST="ghe.example.invalid"

gh_pin_gate() {
  local mock="$1"; shift
  local -a args=("$@")
  if [[ "${args[0]:-}" == api ]]; then
    if [[ "${args[1]:-}" != --hostname || "${args[2]:-}" != github.com ]]; then
      echo "pin gate: 'gh $*' is not pinned to github.com — GH_HOST=${GH_HOST} would redirect it" >&2
      return 97
    fi
    "$mock" api "${args[@]:3}"
    return
  fi
  local i pinned=0
  for (( i = 0; i < ${#args[@]}; i++ )); do
    if [[ "${args[i]}" == --repo || "${args[i]}" == -R ]]; then
      if [[ "${args[i+1]:-}" != github.com/*/* ]]; then
        echo "pin gate: 'gh $*' names a hostless repository — GH_HOST=${GH_HOST} would redirect it" >&2
        return 97
      fi
      args[i+1]="${args[i+1]#github.com/}"
      pinned=1
    fi
  done
  if (( pinned == 0 )); then
    echo "pin gate: 'gh $*' carries no --repo — gh would pick its default repository" >&2
    return 97
  fi
  "$mock" "${args[@]}"
}

gh_hints_pinned() {
  local text="$1" hints
  hints=$(grep -oE "gh (api|auth|pr|run|secret|release) [^'\"()]*" <<<"$text")
  if [[ -z "$hints" ]]; then
    echo "    FAIL: no gh recovery command in: $text" >&2
    return 1
  fi
  if grep -v -- "github.com" <<<"$hints"; then
    echo "    FAIL: the gh command(s) above are not pinned to github.com" >&2
    return 1
  fi
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts): this file only
# defines helpers; running it directly would silently do nothing.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "gh_pin_gate.sh defines test helpers; source it from a release test suite, never run it directly" >&2
  exit 2
fi
