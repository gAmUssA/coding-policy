#!/usr/bin/env bash
# Shared version-comparison helper. Sourced by verify-publish-landed.sh and
# scripts/check-version-bump.sh — defines version_gt() with no side effects and no
# TOP-LEVEL `set` changes, so sourcing it under the caller's `set -euo pipefail`
# is safe (a top-level `set -e` here would leak into the sourcing shell). Direct
# execution is a guarded CLI (entry-point guard at the foot of the file):
# `version-compare.sh <a> <b>` exits 0 if a > b, 1 if not, 2 on bad input, with
# `set -euo pipefail` scoped to that guard.
#
# version_gt <a> <b>: exit 0 iff a > b, comparing numeric MAJOR.MINOR.PATCH.
# Deliberately numeric-only — no prerelease/build precedence — because its
# inputs come from capture-registry-baseline.sh, which emits numeric-only
# versions (a prerelease value would reach (( )) and error rather than order).

version_gt() {
  [[ "$1" != "$2" ]] || return 1
  local a1 a2 a3 b1 b2 b3
  IFS='.' read -r a1 a2 a3 <<< "$1"
  IFS='.' read -r b1 b2 b3 <<< "$2"
  a1=${a1:-0}; a2=${a2:-0}; a3=${a3:-0}
  b1=${b1:-0}; b2=${b2:-0}; b3=${b3:-0}
  (( a1 > b1 )) && return 0
  (( a1 < b1 )) && return 1
  (( a2 > b2 )) && return 0
  (( a2 < b2 )) && return 1
  (( a3 > b3 )) && return 0
  return 1
}

# Direct execution: a guarded CLI (rules/file-hygiene.md Standalone Scripts).
# `set -euo pipefail` is scoped HERE, not at the top level, so sourcing this
# file never enables it in the caller's shell (rules/error-handling.md).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  if [[ $# -ne 2 ]]; then
    echo "usage: $0 <version-a> <version-b>   # exit 0 if a > b, 1 if not, 2 on bad input" >&2
    exit 2
  fi
  for _v in "$1" "$2"; do
    [[ "$_v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] \
      || { echo "error: '$_v' is not numeric MAJOR.MINOR.PATCH — version_gt compares numeric versions only" >&2; exit 2; }
  done
  if version_gt "$1" "$2"; then exit 0; else exit 1; fi
fi
