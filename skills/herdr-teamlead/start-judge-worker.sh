#!/usr/bin/env bash
# Start and verify the judge tier recorded by teamlead plan.
# Contract: <plan-file> <pane> [claude|codex|grok] [--config FILE] [--state FILE] [--task TASK] [--now ISO].
# --config FILE names the operator config the round was planned with (default
# $XDG_CONFIG_HOME/teamlead/config.json); start-judge refuses a plan whose judge
# tier, agent kind or normalized launch_args differ from that file, and launches
# with the file's launch_args.
# Requires an empty shell pane; use apply for an existing worker's relaunch.
# stdout: JSON {agent, model, effort, pane, argv_verified, verified} on success.
# Exit 0: launch arguments proved the tier; 1: input/transport/proof failure;
# 2: command-line usage error. No banner or transcript text establishes proof.
# HERDR_BIN overrides the transport; Python selection is owned by teamlead.sh.
set -euo pipefail

# True when a --config option has no file path after it.
config_value_missing() {
  while (( $# > 0 )); do
    if [[ "$1" == "--config" ]] && { (( $# < 2 )) || [[ "$2" == --* ]]; }; then
      return 0
    fi
    shift
  done
  return 1
}

main() {
  if (( $# < 2 )); then
    echo "start-judge-worker: usage: start-judge-worker.sh <plan-file> <pane> [kind] [--config FILE] [--state FILE] [--task TASK] [--now ISO]" >&2
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
  if config_value_missing "$@"; then
    echo "start-judge-worker: --config needs a file path: pass the config.json the round was planned with." >&2
    return 2
  fi
  bash "${skill_dir}/teamlead.sh" start-judge --assignments "$plan" --pane "$pane" \
    --kind "$kind" --herdr-bin "${HERDR_BIN:-herdr}" "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
