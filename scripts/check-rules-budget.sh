#!/usr/bin/env bash
# Fail when the always-loaded rule files outgrow their byte budget.
#
# Every rules/*.md file loads into every consumer agent session, so their
# total size is an always-on token cost. This gate keeps that total under a
# named budget; text that governs one workflow belongs in that skill's
# references/, which an agent reads only when it needs it.
#
# The budget counts bytes, never characters: the byte count is at least the
# character count, so a total under budget in bytes is under it in characters.
#
# Usage:  scripts/check-rules-budget.sh [rules-dir]
#   rules-dir  directory holding the *.md rule files; defaults to the repo's
#              rules/. The argument exists so the test suite can point the
#              check at a fixture tree.
# Env:    RULES_BUDGET_BYTES overrides the budget, for the test suite only.
# stdout: one JSON object —
#           {"total_bytes": N, "budget_bytes": B, "within_budget": bool,
#            "files": [{"path": "<file>", "bytes": n}, ...]}   (largest first)
# stderr: the actionable diagnostic when over budget or on a setup error.
# Exit:   0 within budget; 1 over budget; 2 setup error (directory missing, no
#         rule files, an unreadable rule file, jq absent, a bad override).

set -euo pipefail

# The budget: rules/ measured 87,927 bytes when this gate landed; 95,000 leaves
# ~8% headroom. Raising it is a policy change for its own PR — move text into
# a skill's references/ first.
readonly DEFAULT_RULES_BUDGET_BYTES=95000

# How many of the largest files the over-budget diagnostic names.
readonly LARGEST_NAMED=5

main() {
  local rules_dir="${1:-}"
  if [[ -z "$rules_dir" ]]; then
    rules_dir="$(CDPATH='' cd -- "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/rules"
  fi
  local budget="${RULES_BUDGET_BYTES:-$DEFAULT_RULES_BUDGET_BYTES}"
  if [[ ! "$budget" =~ ^[1-9][0-9]*$ ]]; then
    echo "check-rules-budget: RULES_BUDGET_BYTES must be a positive integer, got '${budget}' — unset it to use the script's budget" >&2
    return 2
  fi
  if [[ ! -d "$rules_dir" ]]; then
    echo "check-rules-budget: rules directory not found: ${rules_dir} — pass the directory holding the rule files" >&2
    return 2
  fi
  command -v jq >/dev/null 2>&1 \
    || { echo "check-rules-budget: jq not found on PATH — install it ('brew install jq' or 'apt install jq') and rerun" >&2; return 2; }

  local -a files=()
  local f
  shopt -s nullglob
  for f in "$rules_dir"/*.md; do files+=("$f"); done
  shopt -u nullglob
  if (( ${#files[@]} == 0 )); then
    echo "check-rules-budget: no *.md rule files under ${rules_dir} — pass the directory holding the rule files" >&2
    return 2
  fi

  local rows="" size total=0
  for f in "${files[@]}"; do
    if [[ ! -f "$f" || ! -r "$f" ]]; then
      echo "check-rules-budget: cannot read rule file ${f} — restore read access and rerun" >&2
      return 2
    fi
    # wc -c pads its count with spaces on BSD; arithmetic strips them.
    size=$(wc -c < "$f") \
      || { echo "check-rules-budget: cannot size rule file ${f} — restore read access and rerun" >&2; return 2; }
    size=$((size))
    total=$((total + size))
    rows+="$(jq -cn --arg p "${f#"$rules_dir"/}" --argjson b "$size" '{path: $p, bytes: $b}')"$'\n'
  done

  local within=true rc=0
  if (( total > budget )); then within=false; rc=1; fi

  local json
  json=$(printf '%s' "$rows" | jq -cs --argjson t "$total" --argjson b "$budget" --argjson w "$within" \
    '{total_bytes: $t, budget_bytes: $b, within_budget: $w, files: (sort_by(-.bytes, .path))}')
  printf '%s\n' "$json"

  if (( rc != 0 )); then
    local largest
    largest=$(jq -r --argjson n "$LARGEST_NAMED" '[.files[:$n][] | "\(.path) (\(.bytes))"] | join(", ")' <<<"$json")
    echo "check-rules-budget: rules/ totals ${total} bytes, over the ${budget}-byte budget by $((total - budget)) — move workflow-specific text into a skill's references/ (largest: ${largest})" >&2
  fi
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
