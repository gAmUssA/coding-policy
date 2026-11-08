#!/usr/bin/env bash
# Print the Herdr team-round environment for a Rex terminal
# (docs/adr/0016-rex-team-mode-through-herdr-environment.md).
#
# Herdr injects HERDR_ENV and the pane identity into every pane it manages;
# every mode check, hook and rule in this plugin reads them. Rex sets
# REX_SESSION and REX_BLOCK in every terminal instead, so this script maps one
# onto the other and prints the result. The operator starts the lead with it
# (skills/herdr-teamlead/references/herdr.md Running on Rex); workers get the
# same variables from `rex-herdr.sh agent start`.
#
# Contract:
#   argv  : none. Any argument is a usage error.
#   stdout: on success only, one JSON object
#           {"env":{"HERDR_ENV":"1","HERDR_PANE_ID":"<REX_BLOCK>",
#                   "HERDR_WORKSPACE_ID":"<REX_SESSION>","HERDR_BIN":"<adapter>",
#                   "TEAMLEAD_HERDR_BIN":"<adapter>","TEAMLEAD_BACKEND":"rex"}}
#           where <adapter> is the absolute path of the co-shipped rex-herdr.sh.
#   stderr: on failure only, {"error":{"code","message"}} with code
#           `not_in_rex`, `setup_error` or `usage_error`. Every string is
#           JSON-escaped, control bytes included.
#   exit  : 0 success; 1 outside Rex (REX_SESSION or REX_BLOCK unset) or the
#           adapter missing; 2 usage error.
#   env   : reads REX_SESSION and REX_BLOCK. Changes nothing: no export, no
#           file, no Rex call.
set -euo pipefail

# One JSON string literal: quote and backslash escaped, every control byte as
# \u00XX, other bytes passed through.
json_string() {
  local LC_ALL=C s="$1" out="" c code i
  for (( i = 0; i < ${#s}; i++ )); do
    c="${s:i:1}"
    case "$c" in
      '"') out+='\"' ;;
      \\) out+="\\\\" ;;
      *)
        printf -v code '%d' "'$c"
        if (( code >= 0 && code < 32 )) || (( code == 127 )); then
          printf -v c '\\u%04x' "$code"
        fi
        out+="$c"
        ;;
    esac
  done
  printf '"%s"' "$out"
}

fail() { # <exit> <code> <message>
  printf '{"error":{"code":%s,"message":%s}}\n' "$(json_string "$2")" "$(json_string "$3")" >&2
  return "$1"
}

main() {
  if (( $# > 0 )); then
    fail 2 usage_error "rex-team.sh takes no arguments; it prints the team environment as JSON. Start the lead with the one-liner in skills/herdr-teamlead/references/herdr.md Running on Rex"
    return
  fi
  if [[ -z "${REX_SESSION:-}" || -z "${REX_BLOCK:-}" ]]; then
    fail 1 not_in_rex "REX_SESSION and REX_BLOCK are unset; run this from a Rex terminal, in the session that will hold the team"
    return
  fi

  local here skill_dir adapter
  here="${BASH_SOURCE[0]%/*}"
  [[ "$here" == "${BASH_SOURCE[0]}" ]] && here="."
  if ! skill_dir="$(cd "$here" 2>/dev/null && pwd)"; then
    fail 1 setup_error "cannot resolve the skill directory of ${BASH_SOURCE[0]}; reinstall the plugin with \`tessl install gamussa/coding-policy\`"
    return
  fi
  adapter="${skill_dir}/rex-herdr.sh"
  if [[ ! -f "$adapter" ]]; then
    fail 1 setup_error "${adapter} is missing; reinstall the plugin with \`tessl install gamussa/coding-policy\`"
    return
  fi

  local a
  a="$(json_string "$adapter")"
  printf '{"env":{"HERDR_ENV":"1","HERDR_PANE_ID":%s,"HERDR_WORKSPACE_ID":%s,"HERDR_BIN":%s,"TEAMLEAD_HERDR_BIN":%s,"TEAMLEAD_BACKEND":"rex"}}\n' \
    "$(json_string "$REX_BLOCK")" "$(json_string "$REX_SESSION")" "$a" "$a"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
