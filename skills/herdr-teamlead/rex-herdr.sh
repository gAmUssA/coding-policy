#!/usr/bin/env bash
# Herdr-compatible adapter over Rex. Point HERDR_BIN and TEAMLEAD_HERDR_BIN at
# this file to run the team-lead skills inside a Rex session
# (docs/adr/0015-rex-backend-through-herdr-compatible-adapter.md).
#
# Contract:
#   argv  : the Herdr subset listed in skills/herdr-teamlead/teamlead/rex_herdr.py,
#           forwarded verbatim to `uv run … python -m teamlead.rex_herdr`.
#   stdout: one `{"result": ...}` JSON object, reads included; nothing on failure.
#   stderr: `{"error":{"code","message"}}` on every failure. This launcher's own
#           failures, and any uv failure before the module answers, use code
#           `setup_error`.
#   exit  : 0 success; 1 Rex-side or setup failure; 2 usage error.
#   env   : REX_SESSION (required), TEAMLEAD_REX_BIN (default `rex`), UV_BIN
#           (default uv). The module runs through `uv run` per
#           rules/dependency-management.md Python Execution, standard library
#           only, Python 3.11+. PYTHONPATH is prefixed, never replaced.
set -euo pipefail

UV_BIN="${UV_BIN:-uv}"

# Herdr's stderr envelope for a launcher failure, JSON-escaped.
setup_error() { # <message>
  local message="${1//\\/\\\\}"
  message="${message//\"/\\\"}"
  printf '{"error":{"code":"setup_error","message":"%s"}}\n' "$message" >&2
}

ERRFILE=""

# An EXIT trap's last status becomes the script's (rules/error-handling.md).
cleanup() {
  if [[ -n "$ERRFILE" ]] && ! rm -f "$ERRFILE"; then
    echo "rex-herdr: could not remove ${ERRFILE}" >&2
  fi
  return 0
}

main() {
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    setup_error "rex-herdr: '${UV_BIN}' not found on PATH — install uv (https://docs.astral.sh/uv/getting-started/installation/) or point UV_BIN at it"
    return 1
  fi
  if [[ ! -f "${skill_dir}/teamlead/rex_herdr.py" ]]; then
    setup_error "rex-herdr: teamlead/rex_herdr.py is missing from ${skill_dir} — reinstall the plugin with \`tessl install gamussa/coding-policy\`"
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  ERRFILE="$(mktemp -t rex-herdr.XXXXXX)" || { setup_error "rex-herdr: mktemp failed — check that \$TMPDIR is writable"; return 1; }
  trap cleanup EXIT
  local rc=0 first
  "$UV_BIN" run --no-project --no-python-downloads --python '>=3.11' python -m teamlead.rex_herdr "$@" 2>"$ERRFILE" || rc=$?
  if (( rc == 0 )); then
    cat "$ERRFILE" >&2
    return 0
  fi
  # The module answers every failure with one envelope line; anything else
  # came from uv before the module ran.
  if [[ "$(wc -l < "$ERRFILE" | tr -d ' ')" == 1 && "$(head -c 9 "$ERRFILE")" == '{"error":' ]]; then
    cat "$ERRFILE" >&2
    return "$rc"
  fi
  first="$(head -n 1 "$ERRFILE" | head -c 300)"
  setup_error "rex-herdr: uv could not run the adapter (exit ${rc}): ${first} — check \`${UV_BIN} --version\` and that Python 3.11+ is installed"
  return 1
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
