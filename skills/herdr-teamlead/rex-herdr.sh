#!/usr/bin/env bash
# Herdr-compatible adapter over Rex. Point HERDR_BIN and TEAMLEAD_HERDR_BIN at
# this file to run the team-lead skills inside a Rex session
# (docs/adr/0015-rex-backend-through-herdr-compatible-adapter.md).
#
# Contract:
#   argv  : the Herdr subset listed in skills/herdr-teamlead/teamlead/rex_herdr.py,
#           forwarded verbatim to `uv run … python -m teamlead.rex_herdr`.
#   stdout: `{"result": ...}` JSON, or raw pane text for `agent read`/`pane read`.
#   stderr: `{"error":{"code","message"}}` on a Rex-side failure; a diagnostic
#           from this launcher otherwise.
#   exit  : 0 success; 1 Rex-side failure or a missing `uv`; 2 usage error.
#   env   : REX_SESSION (required), TEAMLEAD_REX_BIN (default `rex`), UV_BIN
#           (default uv). The module runs through `uv run` per
#           rules/dependency-management.md Python Execution, standard library
#           only, Python 3.11+. PYTHONPATH is prefixed, never replaced.
set -euo pipefail

UV_BIN="${UV_BIN:-uv}"

main() {
  local skill_dir
  skill_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  if ! command -v "$UV_BIN" >/dev/null 2>&1; then
    echo "rex-herdr: '${UV_BIN}' not found on PATH — install uv (https://docs.astral.sh/uv/getting-started/installation/) or point UV_BIN at it" >&2
    return 1
  fi
  if [[ ! -f "${skill_dir}/teamlead/rex_herdr.py" ]]; then
    echo "rex-herdr: teamlead/rex_herdr.py is missing from ${skill_dir} — reinstall the plugin with \`tessl install gamussa/coding-policy\`" >&2
    return 1
  fi

  export PYTHONPATH="${skill_dir}${PYTHONPATH:+:${PYTHONPATH}}"
  exec "$UV_BIN" run --no-project --no-python-downloads --python '>=3.11' python -m teamlead.rex_herdr "$@"
}

# Entry-point guard (rules/file-hygiene.md Standalone Scripts).
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
