#!/usr/bin/env bash
# Classify whether a resolved publish run published, for an assign-on-merge
# repo (skills/release/SKILL.md Step 7).
#
# A merge with no pending changelog fragment skips the workflow's publish
# step, and so does a run whose earlier step failed. Only a run that concluded
# `success` can answer "no publication"; this script is the one place that
# decision is made, from the run's own conclusion and its single publish step.
#
# Usage: publish-outcome.sh <owner> <repo> <run-id>
#   run-id  the publish run resolve-publish-run.sh returned for the merge
# Env:   GH_BIN  the gh executable (default: gh on PATH; tests point it at a fake)
# Out:   one JSON object on stdout:
#          {"run_id":N,"run_conclusion":"...","publish_step":"...","outcome":"..."}
#        outcome:
#          published       run success, publish step success
#          no_publication  run success, publish step skipped
#          failed          run concluded anything but success, or the publish
#                          step concluded anything but success/skipped
#          ambiguous       the run holds no step, or more than one step,
#                          named PUBLISH_STEP_NAME
# Exit:  0 published or no_publication; 1 failed or ambiguous (stdout carries
#        the JSON, stderr says what to do); 2 indeterminate — gh failed, the
#        payload is unparseable, the run is still in flight, or bad arguments
#        (empty stdout, actionable stderr).

set -euo pipefail

# The workflow step whose conclusion says whether the run published
# (.github/workflows/publish.yml).
PUBLISH_STEP_NAME="Publish"

main() {
  if (( $# != 3 )); then
    echo "usage: publish-outcome.sh <owner> <repo> <run-id>" >&2
    return 2
  fi
  local owner="$1" repo="$2" run_id="$3"
  [[ -n "$owner" && -n "$repo" ]] || { echo "error: owner and repo must be non-empty" >&2; return 2; }
  [[ "$run_id" =~ ^[1-9][0-9]*$ ]] \
    || { echo "error: run id '${run_id}' is not a positive integer — pass the database_id resolve-publish-run.sh printed" >&2; return 2; }
  local gh="${GH_BIN:-gh}"
  command -v "$gh" >/dev/null 2>&1 \
    || { echo "error: gh is not installed or not on PATH — install it (https://cli.github.com) and re-run" >&2; return 2; }
  command -v jq >/dev/null 2>&1 \
    || { echo "error: jq is not installed; install with 'brew install jq' (macOS) or 'apt install jq' (Debian/Ubuntu) and re-run" >&2; return 2; }

  local payload
  payload=$("$gh" run view "$run_id" -R "github.com/${owner}/${repo}" --json status,conclusion,jobs) \
    || { echo "error: gh run view ${run_id} --repo github.com/${owner}/${repo} failed — check 'gh auth status --hostname github.com' and connectivity, then re-run; the outcome is unknown" >&2; return 2; }

  local fields
  fields=$(jq -r --arg step "$PUBLISH_STEP_NAME" '
      [.status // "", .conclusion // "",
       ([.jobs[]?.steps[]? | select(.name == $step)] | length),
       ([.jobs[]?.steps[]? | select(.name == $step) | .conclusion // ""] | first // "")]
      | @tsv' <<<"$payload" 2>/dev/null) \
    || { echo "error: gh run view ${run_id} --repo github.com/${owner}/${repo} returned an unparseable payload — re-run; the outcome is unknown" >&2; return 2; }

  local status conclusion count step
  IFS=$'\t' read -r status conclusion count step <<<"$fields"
  if [[ "$status" != "completed" ]]; then
    echo "error: run ${run_id} is '${status:-unknown}', not completed — watch it to completion (gh run watch ${run_id} --repo github.com/${owner}/${repo}) and re-run" >&2
    return 2
  fi

  local outcome rc=0
  if [[ "$conclusion" != "success" ]]; then
    outcome="failed"; rc=1
    echo "run ${run_id} concluded '${conclusion}' — a failed release; inspect it with gh run view ${run_id} --repo github.com/${owner}/${repo} --log-failed" >&2
  elif [[ "$count" != "1" ]]; then
    outcome="ambiguous"; rc=1
    echo "run ${run_id} has ${count} steps named '${PUBLISH_STEP_NAME}' (expected exactly 1) — check .github/workflows/publish.yml" >&2
  else
    case "$step" in
      success) outcome="published" ;;
      skipped) outcome="no_publication" ;;
      *) outcome="failed"; rc=1
         echo "run ${run_id}'s '${PUBLISH_STEP_NAME}' step concluded '${step}' — a failed release; inspect it with gh run view ${run_id} --repo github.com/${owner}/${repo} --log-failed" >&2 ;;
    esac
  fi

  jq -n --argjson id "$run_id" --arg c "$conclusion" --arg s "$step" --arg o "$outcome" \
    '{run_id: $id, run_conclusion: $c, publish_step: (if $s == "" then null else $s end), outcome: $o}'
  return "$rc"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
