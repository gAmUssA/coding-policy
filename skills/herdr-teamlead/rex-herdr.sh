#!/usr/bin/env bash
# Herdr-compatible adapter over Rex. Point HERDR_BIN and TEAMLEAD_HERDR_BIN at
# this file to run the team-lead skills inside a Rex session
# (docs/adr/0015-rex-backend-through-herdr-compatible-adapter.md).
#
# Contract:
#   argv  : the Herdr subset listed in skills/herdr-teamlead/teamlead/rex_herdr.py,
#           forwarded verbatim to `uv run … python -m teamlead.rex_herdr`.
#   stdout: one `{"result": ...}` JSON object, reads included; nothing on failure.
#   stderr: `{"error":{"code","message"}}` on every failure, and nothing else:
#           the module's own envelope (the last stderr line it writes) passes
#           through; this launcher's failures, and any uv failure before the
#           module answers, use code `setup_error`. Raw uv or interpreter
#           stderr is never forwarded, as it may echo arguments.
#   exit  : 0 success; 1 Rex-side or setup failure; 2 usage error.
#   env   : REX_SESSION (required), TEAMLEAD_REX_BIN (default `rex`), UV_BIN
#           (default uv). The module runs through `uv run` per
#           rules/dependency-management.md Python Execution, standard library
#           only, Python 3.11+. PYTHONPATH is prefixed, never replaced.
set -euo pipefail

UV_BIN="${UV_BIN:-uv}"

# <text> as a JSON string body: quote, backslash and every control byte
# escaped. Bytes run in the C locale, so UTF-8 passes through unchanged.
json_escape() { # <text>
  local LC_ALL=C text="$1" out="" char code i
  for (( i = 0; i < ${#text}; i++ )); do
    char="${text:i:1}"
    case "$char" in
      '"') out+='\"' ;;
      "\\") out+="\\\\" ;;
      *)
        printf -v code '%d' "'$char"
        if (( code >= 0 && code < 32 || code == 127 )); then printf -v char '\\u%04x' "$code"; fi
        out+="$char"
        ;;
    esac
  done
  printf '%s' "$out"
}

# Herdr's stderr envelope for a launcher failure.
setup_error() { # <message>
  printf '{"error":{"code":"setup_error","message":"%s"}}\n' "$(json_escape "$1")" >&2
}

main() {
  local skill_dir
  if ! skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; then
    setup_error "rex-herdr: cannot resolve the launcher's own directory — run it by its full path from the installed plugin"
    return 1
  fi

  if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    setup_error "rex-herdr: '${UV_BIN}' not found on PATH — install uv (https://docs.astral.sh/uv/getting-started/installation/) or point UV_BIN at it"
    return 1
  fi
  if [[ ! -f "${skill_dir}/teamlead/rex_herdr.py" ]]; then
    setup_error "rex-herdr: teamlead/rex_herdr.py is missing from ${skill_dir} — reinstall the plugin with \`tessl install gamussa/coding-policy\`"
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  local rc=0 err last
  # stdout passes straight through on fd 3; stderr is captured, never forwarded.
  { err="$("$UV_BIN" run --no-project --no-python-downloads --python '>=3.11' python -m teamlead.rex_herdr "$@" 2>&1 1>&3 3>&-)" || rc=$?; } 3>&1
  if (( rc == 0 )); then
    return 0
  fi
  # The module ends every failure with one JSON-encoded envelope line; uv or
  # interpreter warnings may precede it. No envelope means uv failed first.
  last="${err##*$'\n'}"
  if [[ "$last" == '{"error":'* ]]; then
    printf '%s\n' "$last" >&2
    return "$rc"
  fi
  setup_error "rex-herdr: uv could not run the adapter (exit ${rc}) — run \`${UV_BIN} run --no-project --no-python-downloads --python '>=3.11' python -V\` to see uv's error, and check that Python 3.11+ is installed"
  return 1
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
