#!/usr/bin/env bash
# Fail when a tracked text file carries a leftover merge-conflict marker.
#
# A hand-resolved conflict can leave a marker line behind that every other
# check passes — a CHANGELOG or changelog fragment is plain prose to lint and
# tests alike. This gate scans every file `git ls-files` tracks.
#
# A marker line is seven `<`, `|` or `>` at column 0, followed by a space or
# the end of the line (git's start, diff3 base, and end markers). A bare
# `=======` is never flagged: Markdown uses it as a setext heading underline,
# and every conflict git writes also carries a start and an end marker.
# Binary files (git's own detection, `git grep -I`) are skipped.
#
# Usage:  scripts/check-conflict-markers.sh [repo-dir]
#   repo-dir  the git checkout to scan; defaults to this script's repository.
#             The argument exists so the test suite can point it at a fixture.
# stdout: one JSON object —
#           {"clean": bool, "markers": [{"path": "<file>", "line": n, "text": "<line>"}]}
# stderr: the actionable diagnostic when markers are found or on a setup error.
# Exit:   0 clean; 1 markers found; 2 setup error (not a git checkout, git or
#         jq absent, git grep failing).

set -euo pipefail

# Start, diff3 base, and end markers; never the `=======` separator alone.
readonly MARKER_RE='^(<{7}|[|]{7}|>{7})( |$)'

HITS_FILE=""

# The EXIT trap's last status becomes the script's; end with return 0 so a
# failed rm never rewrites the verdict.
cleanup() {
  if [[ -n "$HITS_FILE" ]] && ! rm -f "$HITS_FILE"; then
    echo "check-conflict-markers: warning: could not remove ${HITS_FILE} — remove it by hand" >&2
  fi
  return 0
}

main() {
  if (( $# > 1 )); then
    echo "usage: check-conflict-markers.sh [repo-dir]" >&2
    return 2
  fi
  local repo="${1:-}"
  if [[ -z "$repo" ]]; then
    repo="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  fi
  command -v git >/dev/null 2>&1 \
    || { echo "check-conflict-markers: git not found on PATH — install git and rerun" >&2; return 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "check-conflict-markers: jq not found on PATH — install it ('brew install jq' or 'apt install jq') and rerun" >&2; return 2; }
  if ! git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "check-conflict-markers: ${repo} is not a git checkout — run the check from the repository root or pass its path" >&2
    return 2
  fi

  # git grep exits 0 on a match, 1 on none, anything else on an error.
  # -z separates path and line number with NUL, so any path is safe; the
  # output goes to a file because a shell variable cannot hold a NUL.
  HITS_FILE=$(mktemp) || { echo "check-conflict-markers: mktemp failed — set TMPDIR to a writable directory and rerun" >&2; return 2; }
  trap cleanup EXIT
  local rc=0
  git -C "$repo" grep -z -n -I -E "$MARKER_RE" -- . > "$HITS_FILE" || rc=$?
  case "$rc" in
    0) ;;
    1) printf '%s\n' '{"clean":true,"markers":[]}'; return 0 ;;
    *) echo "check-conflict-markers: git grep failed (exit ${rc}) in ${repo} — rerun it by hand to see git's error" >&2; return 2 ;;
  esac

  # Each hit is "<path>NUL<line>NUL<text>\n"; the text keeps any later NUL.
  local json
  json=$(jq -R -s -c '
    [split("\n")[] | select(length > 0) | split("\u0000")
     | {path: .[0], line: (.[1] | tonumber), text: (.[2:] | join("\u0000"))}]
    | {clean: false, markers: .}' "$HITS_FILE") \
    || { echo "check-conflict-markers: could not parse git grep's output — rerun 'git grep -n -E \"${MARKER_RE}\"' by hand" >&2; return 2; }
  printf '%s\n' "$json"
  local where
  where=$(jq -r '[.markers[] | "\(.path):\(.line)"] | join(", ")' <<<"$json")
  echo "check-conflict-markers: leftover merge-conflict marker(s) at ${where} — finish resolving the conflict and delete the marker lines" >&2
  return 1
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
