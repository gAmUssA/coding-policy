#!/usr/bin/env bash
# Build one immutable VCS review artifact for an explicitly recorded range.
#
# Usage: review-package.sh BASE HEAD [OUTFILE]
# Run in a checkout containing both commits. BASE is the recorded pre-round
# tip, not a guessed HEAD~1. Refs are resolved once to full commit IDs.
# With no OUTFILE, TEAMLEAD_REPORTS_DIR is required; the filename is
# review-<base7>..<head7>.diff. An explicit OUTFILE selects the reports path.
# stdout: only the absolute written path, followed by a newline (not JSON).
# stderr: diagnostics. Exit 0: written or identical artifact already present;
# exit 2: usage, invalid ref, or invalid output path; exit 1: tool/I/O failure.
# Existing different files and symlinks are never overwritten. Git failures
# leave no partial artifact. Sourcing defines the helpers and enables strict
# shell options; only direct execution builds a package.
set -euo pipefail

review_package_warn() { printf 'review-package: %s\n' "$1" >&2; }

review_package_content() { # <base-sha> <head-sha>
  local range="$1..$2"
  printf 'Review package\nBASE: %s\nHEAD: %s\n\n## Commits\n' "$1" "$2" || return 1
  git --no-pager log --no-color --no-decorate --no-show-signature \
    --reverse --topo-order --format='%H %s' "$range" -- || return 1
  printf '\n## Stat\n' || return 1
  git --no-pager diff --no-color --no-ext-diff --no-textconv \
    --no-renames --no-relative --stat=80 --stat-count=0 "$range" -- || return 1
  printf '\n## Diff\n' || return 1
  git --no-pager diff --no-color --no-ext-diff --no-textconv \
    --no-renames --no-relative --src-prefix=a/ --dst-prefix=b/ \
    --diff-algorithm=myers --no-indent-heuristic --inter-hunk-context=0 \
    --submodule=short --binary --full-index -U10 "$range" -- || return 1
}

main() (
  if (( $# < 2 || $# > 3 )); then
    review_package_warn 'usage: review-package.sh BASE HEAD [OUTFILE]; record BASE before the round'
    return 2
  fi
  if ! command -v git >/dev/null 2>&1; then
    review_package_warn 'git is missing — install Git and re-run from the target checkout'
    return 1
  fi
  if ! git rev-parse --git-dir >/dev/null 2>&1; then
    review_package_warn 'no readable Git repository — run from a checkout containing BASE and HEAD'
    return 1
  fi
  local base head outfile directory filename package_dir='' package_tmp=''
  base="$(git rev-parse --verify --end-of-options "${1}^{commit}")" || {
    review_package_warn "invalid BASE '${1}' — fetch the recorded pre-round commit and pass its SHA"
    return 2
  }
  head="$(git rev-parse --verify --end-of-options "${2}^{commit}")" || {
    review_package_warn "invalid HEAD '${2}' — fetch the pushed branch tip and pass its SHA"
    return 2
  }
  if (( $# == 3 )); then
    outfile="$3"
  elif [[ -n "${TEAMLEAD_REPORTS_DIR:-}" ]]; then
    outfile="${TEAMLEAD_REPORTS_DIR}/review-${base:0:7}..${head:0:7}.diff"
  else
    review_package_warn 'set TEAMLEAD_REPORTS_DIR to the round reports directory, or pass OUTFILE there'
    return 2
  fi
  if [[ -z "$outfile" || "$outfile" == */ || "$outfile" == *[[:cntrl:]]* ]]; then
    review_package_warn 'OUTFILE must name a file on one line — choose a path in the round reports directory'
    return 2
  fi
  # Prefix relative paths before passing them to Unix tools, including -names.
  [[ "$outfile" == /* ]] || outfile="$PWD/$outfile"
  directory="$(dirname "$outfile")"
  filename="$(basename "$outfile")"
  if [[ "$filename" == . || "$filename" == .. || -L "$outfile" || -d "$outfile" ]]; then
    review_package_warn "unsafe output target ${outfile} — choose a regular artifact file, not a directory or symlink"
    return 2
  fi
  if ! mkdir -p "$directory"; then
    review_package_warn "cannot create ${directory} — choose a writable reports directory"
    return 1
  fi
  directory="$(cd "$directory" && pwd -P)" || return 1
  outfile="$directory/$filename"
  package_dir="$(mktemp -d "$directory/.review-package.XXXXXX")" || {
    review_package_warn "cannot create a temporary artifact in ${directory} — check permissions"
    return 1
  }
  package_tmp="$package_dir/$filename"
  # The EXIT trap invokes this local helper indirectly.
  # shellcheck disable=SC2329
  cleanup() {
    if [[ -n "$package_tmp" ]] && ! rm -f "$package_tmp"; then
      review_package_warn "could not remove temporary file ${package_tmp} — remove it after inspection"
    fi
    if [[ -n "$package_dir" ]] && ! rmdir "$package_dir"; then
      review_package_warn "could not remove staging directory ${package_dir} — inspect and remove it by hand"
    fi
    return 0
  }
  trap cleanup EXIT
  if ! review_package_content "$base" "$head" > "$package_tmp"; then
    review_package_warn 'Git could not build the complete package — fix the reported error and re-run; no artifact published'
    return 1
  fi
  if [[ -e "$outfile" || -L "$outfile" ]]; then
    if [[ ! -L "$outfile" && -f "$outfile" ]]; then
      local compare_rc=0
      cmp -s "$package_tmp" "$outfile" || compare_rc=$?
      case "$compare_rc" in
        0) printf '%s\n' "$outfile"; return 0 ;;
        1) ;; # Expected comparison result: different content.
        *)
          review_package_warn "cannot compare ${outfile} (cmp exit ${compare_rc}) — check file permissions and the cmp installation, then re-run; existing artifact preserved"
          return 1
          ;;
      esac
    fi
    review_package_warn "${outfile} already holds different content — keep it and choose a new range-specific OUTFILE"
    return 1
  fi
  # Linking into the parent uses the staged basename. If that basename became
  # a directory, ln refuses instead of treating it as a second destination.
  if ! ln "$package_tmp" "$directory/"; then
    review_package_warn "cannot publish ${outfile} — check permissions or an artifact created concurrently, then re-run"
    return 1
  fi
  printf '%s\n' "$outfile"
)

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
