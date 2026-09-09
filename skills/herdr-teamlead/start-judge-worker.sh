#!/usr/bin/env bash
# Start and verify the judge tier recorded by teamlead plan.
# Contract: <plan-file> <pane> [claude|codex|grok] [--state FILE] [--task TASK] [--now ISO].
# Requires an empty shell pane; use apply for an existing worker's relaunch.
# stdout: JSON {agent, model, effort, pane, argv_verified, verified} on success.
# Exit 0: launch arguments proved the tier; 1: input/transport/proof failure;
# 2: command-line usage error. No banner or transcript text establishes proof.
# HERDR_BIN overrides the transport; Python selection is owned by teamlead.sh.
set -euo pipefail

main() {
  if (( $# < 2 )); then
    echo "start-judge-worker: usage: start-judge-worker.sh <plan-file> <pane> [kind] [--state FILE] [--task TASK] [--now ISO]" >&2
    return 2
  fi
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local plan="$1" pane="$2" kind="claude"
  shift 2
  if (( $# > 0 )) && [[ "$1" != --* ]]; then
    kind="$1"
    shift
  fi
  bash "${skill_dir}/teamlead.sh" start-judge --assignments "$plan" --pane "$pane" \
    --kind "$kind" --herdr-bin "${HERDR_BIN:-herdr}" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
