#!/usr/bin/env bash
# Keep the git history an eval scenario's input repo needs, as text.
#
# Some scenarios under evals/ hand the agent a git repository (a feature
# branch to ship, a history to read). Their nested `.git` directories cannot be
# committed to this repo: git records an embedded repository as a bare gitlink
# and drops its history. Each fixture's history is stored instead as a
# `git fast-export` stream — plain text, diffable — and rebuilt before a run.
#
# Manifest: evals/git-fixtures.tsv, one fixture per line:
#   <target-dir>\t<head-ref>\t<stream-file>
#   target-dir   the repo's working tree, relative to the repo root
#   head-ref     the branch HEAD points at (refs/heads/...)
#   stream-file  where the fast-export stream lives, relative to the repo root
#
# Usage:
#   eval-fixtures.sh export   write each fixture's stream and head ref from its .git
#   eval-fixtures.sh import   rebuild each missing .git from its stream; a fixture
#                             whose .git already exists is left alone
# Run `import` before `tessl eval run .`; run `export` after changing a fixture's
# history, then delete the fixture's .git before committing.
#
# Out:  one JSON object: {"command": "...", "fixtures": [{"target": ..., "action": ...}]}
# Exit: 0 ok; 1 a fixture is inconsistent (its rebuilt tree differs from the
#       committed files, or its .git is missing on export); 2 usage or tool error.

set -euo pipefail

MANIFEST="evals/git-fixtures.tsv"

die() { echo "eval-fixtures: $1" >&2; exit "${2:-2}"; }

read_manifest() {
  [[ -f "$MANIFEST" ]] || die "no ${MANIFEST} — run from the repo root"
  local target head stream
  while IFS=$'\t' read -r target head stream || [[ -n "$target" ]]; do
    [[ -z "$target" || "$target" == \#* ]] && continue
    [[ -n "$head" && -n "$stream" ]] || die "${MANIFEST}: '${target}' needs <target>\\t<head-ref>\\t<stream>"
    [[ "$head" == refs/heads/* ]] || die "${MANIFEST}: head ref '${head}' for ${target} must be refs/heads/<branch>"
    printf '%s\t%s\t%s\n' "$target" "$head" "$stream"
  done < "$MANIFEST"
}

cmd_export() {
  local rows target head stream results=()
  rows=$(read_manifest)
  while IFS=$'\t' read -r target head stream; do
    [[ -n "$target" ]] || continue
    [[ -d "$target/.git" ]] || die "${target}/.git is missing — run 'eval-fixtures.sh import' first" 1
    mkdir -p "$(dirname "$stream")"
    git -C "$target" fast-export --all --signed-tags=strip --tag-of-filtered-object=drop > "$stream" \
      || die "git fast-export failed in ${target}"
    [[ "$(git -C "$target" symbolic-ref HEAD)" == "$head" ]] \
      || die "${target} HEAD is $(git -C "$target" symbolic-ref HEAD), manifest says ${head} — update ${MANIFEST}" 1
    results+=("$(jq -n --arg t "$target" '{target: $t, action: "exported"}')")
  done <<< "$rows"
  jq -n --argjson f "$(printf '%s\n' "${results[@]+"${results[@]}"}" | jq -s .)" '{command: "export", fixtures: $f}'
}

cmd_import() {
  local rows target head stream results=() dirty
  rows=$(read_manifest)
  while IFS=$'\t' read -r target head stream; do
    [[ -n "$target" ]] || continue
    if [[ -d "$target/.git" ]]; then
      results+=("$(jq -n --arg t "$target" '{target: $t, action: "present"}')")
      continue
    fi
    [[ -d "$target" ]] || die "${target} does not exist — the fixture's working tree is committed with the scenario"
    [[ -f "$stream" ]] || die "stream ${stream} for ${target} is missing — restore it or re-run export from a fixture with .git"
    git -C "$target" init -q \
      || die "git init failed in ${target}"
    git -C "$target" fast-import --quiet < "$stream" \
      || die "git fast-import failed in ${target}"
    git -C "$target" symbolic-ref HEAD "$head" \
      || die "cannot point HEAD at ${head} in ${target}"
    git -C "$target" reset -q \
      || die "git reset failed in ${target}"
    dirty=$(git -C "$target" status --porcelain) \
      || die "git status failed in ${target}"
    if [[ -n "$dirty" ]]; then
      echo "eval-fixtures: ${target} differs from ${head} after import:" >&2
      printf '%s\n' "$dirty" >&2
      die "re-run export from the intended history, or restore the committed files" 1
    fi
    results+=("$(jq -n --arg t "$target" '{target: $t, action: "imported"}')")
  done <<< "$rows"
  jq -n --argjson f "$(printf '%s\n' "${results[@]+"${results[@]}"}" | jq -s .)" '{command: "import", fixtures: $f}'
}

main() {
  (( $# == 1 )) || die "usage: eval-fixtures.sh export|import"
  command -v git >/dev/null 2>&1 || die "git is not installed or not on PATH"
  command -v jq >/dev/null 2>&1 \
    || die "jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run"
  case "$1" in
    export) cmd_export ;;
    import) cmd_import ;;
    *) die "usage: eval-fixtures.sh export|import" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
