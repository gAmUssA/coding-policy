#!/usr/bin/env bash
# Start a Herdr team round inside Rex
# (docs/adr/0016-rex-team-mode-through-herdr-environment.md).
#
# Herdr injects HERDR_ENV and the pane identity into every pane it manages;
# every mode check, hook and rule in this plugin reads them. Rex sets
# REX_SESSION and REX_BLOCK in every terminal instead, so this launcher maps
# one onto the other and execs the given command, typically the lead agent.
# Workers get the same variables from `rex-herdr.sh agent start`.
#
# Contract:
#   argv  : <command> [args...] — exec'd with the environment below.
#   stdout: the command's own.
#   stderr: a diagnostic when a precondition fails; the command's own otherwise.
#   exit  : the command's own status; 1 outside Rex, with no command, or when
#           the co-shipped rex-herdr.sh is missing.
#   env   : reads REX_SESSION and REX_BLOCK (both required). Exports
#           HERDR_ENV=1, HERDR_PANE_ID=$REX_BLOCK, HERDR_WORKSPACE_ID=$REX_SESSION,
#           HERDR_BIN and TEAMLEAD_HERDR_BIN (absolute path of rex-herdr.sh),
#           and TEAMLEAD_BACKEND=rex.
set -euo pipefail

main() {
  if [[ -z "${REX_SESSION:-}" || -z "${REX_BLOCK:-}" ]]; then
    echo "rex-team: REX_SESSION and REX_BLOCK are unset — run this from a Rex terminal, in the session that will hold the team" >&2
    return 1
  fi
  if (( $# == 0 )); then
    echo "rex-team: name the command to start, e.g. \`rex-team.sh claude\` for the lead" >&2
    return 1
  fi

  local skill_dir adapter
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  adapter="${skill_dir}/rex-herdr.sh"
  if [[ ! -f "$adapter" ]]; then
    echo "rex-team: ${adapter} is missing — reinstall the plugin with \`tessl install gamussa/coding-policy\`" >&2
    return 1
  fi

  export HERDR_ENV=1
  export HERDR_PANE_ID="$REX_BLOCK"
  export HERDR_WORKSPACE_ID="$REX_SESSION"
  export HERDR_BIN="$adapter"
  export TEAMLEAD_HERDR_BIN="$adapter"
  export TEAMLEAD_BACKEND=rex
  exec "$@"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
